# The issued-invoice layout — the design, and why it is this one

**Status:** adopted, WO-97. **Renderer:** `backend/app/services/invoice_pdf.py::build_pdf`.
**Decided by:** the owner, from three fully rendered A4 candidates.

This is the record the design review owed and never wrote. It exists because the
next person to touch this file needs to know which of its choices are taste
(change them freely) and which are load-bearing (change them and something
breaks that no test will tell you about).

---

## 1. Why this page gets its own document

The issued invoice PDF is **the only artefact of this product a customer's
customer ever sees**. Every other surface — the dashboards, the approval inbox,
the VAT workbench — is seen by the person who already bought the product. This
page is seen by the person who pays the bill.

That makes it the only screen in the codebase where "it renders correct figures"
is not the finish line.

## 2. The three candidates

One synthetic dataset, three complete A4 pages, rendered side by side so the
comparison was about layout and nothing else.

| | Idea | Strength | Why not |
|---|---|---|---|
| **A — refined ledger** | The familiar invoice structure, disciplined: labelled metadata strip, hairline table, right-aligned totals with the total set apart | Reads like an accounting document. Nothing to relearn. Survives a photocopier. Degrades gracefully as lines multiply | — **chosen** |
| **B — payment first** | A boxed hero band answering *how much* and *by when* before anything else; the line items become supporting evidence beneath | Genuinely faster for a payer skimming a stack | The band relies on a fill to work; it prints as grey mush. And it subordinates the line detail, which is exactly what a disputing customer came for |
| **C — typographic** | No rules, no fills. Hierarchy from type size, weight and whitespace alone | The quietest on paper; ages best | Too quiet for a document read under time pressure. Wide whitespace also collapses badly once a line description wraps to four lines |

**A was chosen.** The reasoning that decided it: an invoice is not a landing
page. Its job is to be unambiguous to a stranger, at speed, in whatever degraded
form it reaches them — forwarded as a scan, printed on a mono laser, photocopied
into an audit file. B optimises for the best case and A for the worst, and the
worst case is the one that generates support tickets and late payments.

## 3. The rules that are load-bearing

Change these only with a reason better than preference.

**No meaning is carried by colour alone.** The previous renderer filled the line
table's header with brand blue and zebra-striped the rows. In greyscale the
zebra becomes indistinguishable banding, and for a reader with a colour-vision
deficiency it never worked. Structure now comes from rules, weight and space.
There is one ink, one muted and one rule colour, and the page is legible in pure
black and white.

**Every amount states its currency.** `_money` suffixes the invoice currency to
every figure. It is repetitive by design: a page separated from page 1 must not
leave a number whose unit is a guess.

**Numeric columns are right-aligned in the paragraph, not by the table.** A
reportlab cell containing a `Paragraph` ignores `ALIGN` — it aligns the
paragraph's box, which already fills the cell. The approved prototype had this
bug and rendered its figures flush left. Alignment lives in the paragraph style
(`alignment=2`). Without it the decimal points do not stack, and a column of
money that does not stack is a column a reader cannot scan.

**The frame has zero padding.** `SimpleDocTemplate`'s default `Frame` carries 6pt
of padding, and a full-width table is *centred* over that padding — so tables
land at the margin while bare paragraphs land 6pt inboard. The result was a
payment block visibly misaligned with the table above it. The renderer builds its
own `BaseDocTemplate` + zero-padded `Frame` so the 18mm margin is the single true
left edge for everything.

**The totals are a row of the line table, not a flowable after it.** This is the
subtlest one and the most important:

> `KeepTogether` stops the totals block being **split**. It does not bind it to
> the table. At 30 lines the last item ended at the foot of page 2 and the whole
> totals block landed alone on page 3.

The totals are therefore the final, column-spanning row of the line table, and a
`NOSPLIT` style command covers *(last line item, totals)* so a break cannot fall
between them — if the totals will not fit, the last line is carried onto the new
page with them, under the repeated column header. The reader never turns a page
to find a total floating with nothing it totals.

`rowSplitRange` looks like the right tool and is not: reportlab drops it from the
continuation table after the first split, so it constrains page 2 and nothing
after. `NOSPLIT` is remapped across every split. This is verified by
`test_totals_are_never_orphaned_from_the_lines_they_total`, which sweeps line
counts 18–29; removing the `NOSPLIT` command fails it at 18, 19 and 20.

**Page numbering is real or absent.** `Page n of m` comes from a canvas that
buffers pages and writes them only once it knows the count. It is drawn **only**
when `m > 1`: on a one-page invoice it is noise, and a decorative count that
does not match the document is a statement a reader cannot check. Continuation
pages repeat the document heading and number so a detached sheet is
identifiable.

**Compliance text is conditional on being true.** The Factur-X line (`a9f6be9` /
`c1e5ee8`) prints only when XML is really embedded, and an empty attachment is
never written. The same principle governs `Total credited` on a credit note
(which is not payable) and the `DUE` column (dropped, not printed blank, when
there is no due date).

**The logo gate fails open.** A corrupt or unreadable logo is logged and
dropped. The document is legally complete without a mark and incomplete without
the figures, so open is the only safe direction.

## 4. Where each Art. 226 element lives

Directive 2006/112/EC Art. 226. Asserted element-by-element by
`test_every_article_226_element_is_findable_on_one_rendered_invoice`.

| Art. 226 | Element | Where it lives now |
|---|---|---|
| (1) | Date of issue | Metadata strip, `ISSUED` |
| (2) | Sequential number | Metadata strip, `INVOICE NO.` / `CREDIT NOTE NO.` (bold — the field a payer must quote) |
| (3) | Supplier VAT identification number | Header, seller block, `VAT … · Reg …` |
| (4) | Customer VAT identification number | Metadata strip, `BILL TO`, last line of the buyer block |
| (5) | Full name and address of supplier and customer | Header seller block / `BILL TO` buyer block |
| (6) | Quantity and nature of the goods or services | Line table, `DESCRIPTION` + `QTY` |
| (7) | Date of supply, where determined and different from the issue date | The quiet line under the strip, `Supply date …` — **printed only when it differs**, which is exactly when it is mandatory |
| (8) | Taxable amount per rate, unit price ex-VAT, discounts not in the unit price | Totals, `VAT n% of <base>` (one row per rate); line table `UNIT PRICE`; line table `DISC %` (shown whenever any line carries one) |
| (9) | VAT rate applied | Line table, `VAT` |
| (10) | VAT amount payable | Totals, the value on each `VAT n% of …` row |
| (11) | Exemption reference, where VAT is not charged | The note directly beneath the totals, inside the same unbreakable block — `tax_exemption_reason` (EN-16931 BT-120) if set, else the scheme note |

Sequential numbering, immutability and the credit-note mechanism are enforced
upstream in `issued_service` / `numbering`, not by the renderer.

## 5. Known gaps (deliberately not fixed here)

- **The corrected invoice's number is not printed on a credit note.** Art. 219
  treats a corrective document as referring to the original. `build_pdf` receives
  `corrected_invoice_id` but not the corrected invoice, and wiring a DB read into
  a pure renderer is a signature change. Own work order.
- **`issued_invoices.note` is stored and never printed.** A free-text buyer note
  that never reaches the buyer. A content change, not a layout one.
- **The compliance sentence says "Invoice compliant…" on a credit note.** Cosmetic
  and pre-existing; changing the string would touch the assertion set of
  `test_invoice_pdf_facturx_claim.py`, which WO-97 deliberately left untouched.
- **Country is rendered as the raw ISO code** (`EE`, not `Estonia`), unchanged
  from before. Correct but terse; a country-name mapping is a separate decision.
- **Not strict PDF/A-3.** Colour profile and XMP conformance remain a hardening
  step, as the module docstring has always said.

## 6. How to verify a change to this file

Tests are necessary and **not sufficient** — every assertion in
`tests/test_invoice_pdf_layout.py` passes on a page whose columns collide,
whose figures wrap mid-number, or whose rules sit in the wrong place. Text
extraction cannot see layout.

So: render, rasterise, and **look**. The cases that have caught real defects are
the short invoice, a 30-line invoice across three pages, three VAT rates, a
credit note, an explicit exemption reason, reverse charge, a logo, a seven-figure
amount, a negative amount, long company names and descriptions, no due date, and
a line count tuned to break right at the page boundary.

```python
# render to PDF, then:
import pypdfium2 as pdfium
book = pdfium.PdfDocument(path)
for i in range(len(book)):
    book[i].render(scale=2.0).to_pil().save(f"{name}-p{i + 1}.png")
```

`pypdfium2` is a local verification tool only — it is deliberately **not** a
project dependency and must not be added to `requirements.txt`.
