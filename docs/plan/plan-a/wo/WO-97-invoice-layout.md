# WO-97 — the issued-invoice PDF layout (design A, "refined ledger")

**Effort S (1–2d). Priority P2. Milestone M4. Depends on: WO-46 (multi-issuer
`seller_json` snapshot), the Factur-X conditional-claim fix `a9f6be9` / `c1e5ee8`
(`tests/test_invoice_pdf_facturx_claim.py`), and the owner's design review of the
three rendered candidates (A / B / C) — **A chosen**.**

---

## RECON — verified before any code

### 1. What the renderer is today

`backend/app/services/invoice_pdf.py::build_pdf` (315 lines, one function) draws
the customer-facing invoice with reportlab platypus and then attaches the
Factur-X CII via pypdf. Verified structure, top to bottom:

| Band | Current implementation | Line |
|---|---|---|
| Header | `Table([[left, meta]])` — left cell = optional logo + seller block; right cell = a 5-row single-column table stacking `INVOICE` (22pt, brand blue, right), number, issue date, due date, PO | 109–167 |
| Bill-to | own table, label `BILL TO` at 8pt muted | 169–185 |
| Lines | `#` / Description / Qty / Unit price / [Disc %] / VAT % / Net; **solid brand-blue header band, white text**, zebra `ROWBACKGROUNDS`, 0.4pt rule under every row | 187–226 |
| Summary | `Table([[vat_tbl, totals]])` — VAT breakdown (rate/base/vat) on the LEFT, a 3-row totals block on the RIGHT | 228–267 |
| Note | `tax_exemption_reason` or `SCHEME_NOTES[vat_scheme]`, bold small | 269–273 |
| Payment | IBAN/BIC, payment instructions, contact, seller notes — one muted paragraph | 275–287 |
| Claim | `"Invoice compliant with EN 16931 · Factur-X XML embedded."` — **only when `xml_bytes` is non-empty** (`c1e5ee8`) | 288–299 |

Margins are 16mm/16mm/18mm/16mm. `repeatRows=1` is already set on the line table.
There is **no** page footer and **no** `KeepTogether` anywhere.

### 2. The three defects the redesign fixes

1. **The metadata is a stack, not a strip.** Number / issue date / due date / PO
   are five right-aligned rows in a 70mm column (110–133), each in its own
   throwaway `ParagraphStyle`. Nothing is labelled except by the sentence prefix
   ("Issue date: …"), so the number — the single field a payer must quote — has
   no more visual weight than the PO reference.
2. **Colour is carrying structure.** The line-table header is a filled
   `_BRAND` band with white text and the rows are zebra-striped (213–220). On a
   monochrome print, and for the ~8% of male readers with a colour-vision
   deficiency, the zebra becomes indistinguishable grey banding. A financial
   document has to survive being faxed, photocopied and printed on an office
   mono laser.
3. **The totals block can be orphaned.** Nothing keeps `Subtotal / VAT / Total`
   attached to the table above it, so a 24-line invoice can page-break with the
   last line at the foot of page 1 and the entire totals block alone on page 2,
   with no repeated header and no page numbering to tell a reader that page 2
   belongs to the same document.

### 3. The chosen design

Three full A4 candidates were rendered from one synthetic dataset and reviewed:

- **A — "refined ledger"**: the existing structure, disciplined. Labelled
  metadata strip, hairline table, right-aligned totals with the total set apart.
- **B — "payment first"**: a boxed hero band answering *how much* / *by when*
  before anything else.
- **C — "typographic"**: no rules, no fills; hierarchy from type and whitespace
  alone.

The owner chose **A**. Rationale and the trade-offs recorded in
`docs/design/invoice-layout.md` (this order authors that file).

### 4. What the prototype did NOT cover, and therefore what this order carries

The prototype was a single-page, single-rate, no-logo, invoice-only sketch. The
production renderer must additionally handle, and this order must verify by eye:

| Case | Source of truth |
|---|---|
| Multiple VAT rates | `vat.VatResult.breakdown: list[RateBucket]` — the prototype hard-coded one 22% row |
| Line discounts | `li["discount_percent"]`, column shown only when non-zero — **Art. 226(8) mandatory when not included in the unit price** |
| Page breaks, 25+ lines | `repeatRows=1` + a `KeepTogether` totals block + a real "Page n of m" |
| Credit notes | `invoice.doc_type == "credit_note"` |
| Exemption reason | `invoice.tax_exemption_reason` (EN-16931 BT-120) or `vat.SCHEME_NOTES[vat_scheme]` |
| Reverse charge / intra-EU / exempt | `vat.ZERO_VAT_SCHEMES` — every rate forced to 0 |
| Logo | `logo: tuple[mime, bytes] | None` from the invoice's own issuer entity |
| Conditional Factur-X claim | `xml_bytes` truthiness — `c1e5ee8`, must not regress |
| Long names / long descriptions | `buyer_name` 200 chars, `description` clamped to 500 by `vat.compute` |
| Seven-figure and negative amounts | `Money = Numeric(14,2)`; a credit note's figures are positive but a corrective line can be negative |

---

## Objective and business value

The issued invoice PDF is the **only artefact of this product a customer's
customer ever sees**. Every other surface is internal. Today it renders correct
figures inside a layout that leans on a filled blue header band and zebra
striping to carry its structure (`invoice_pdf.py:213–220`), stacks five unlabelled
metadata rows where the invoice number should dominate (110–133), and can push
its totals block onto a second page with nothing holding it to the table it
totals (no `KeepTogether` anywhere in the module). None of that is a wrong
number; all of it is a document that reads as generated rather than issued.

Who pays more because of it: the SME finance lead choosing between InvoiceIQ and
an incumbent compares exactly one visible output — the PDF their client will
receive. A document that photocopies to grey mush, or that arrives with page 2
holding nothing but a total, is the reason a trial does not convert. This order
changes no figure, no schema and no route; it changes the one page that gets
forwarded to the person who signs the payment.

## Scope

**In scope:**
- Rewrite the body of `app/services/invoice_pdf.py::build_pdf` to design A.
- A real `Page n of m` footer, drawn only when the document exceeds one page,
  carrying the document number so a detached sheet is identifiable.
- Keep the totals block, the exemption note and the payment block from being
  split or orphaned (`KeepTogether`).
- Preserve the conditional discount column (Art. 226(8)).
- Print `supply_date` when it is set **and differs from** `issue_date`
  (Art. 226(7) — mandatory in exactly that case, and absent from the renderer
  today; the model column `issued_invoices.supply_date` already exists).
- New test module `tests/test_invoice_pdf_layout.py` asserting the structural
  facts a text-extraction test *can* assert: label presence, per-rate breakdown
  rows, page count and repeated header on a 30-line invoice, page numbering,
  credit-note heading, exemption reason, zero-VAT scheme.
- `docs/design/invoice-layout.md` — the design record (chosen option and why).

**Out of scope:**
- The Factur-X/CII payload itself (`facturx.py`, `einvoice.py`) — unchanged.
- PDF/A-3 conformance (colour profile, XMP). Still a hardening step; the module
  docstring already says so and continues to.
- Rendering `issued_invoices.note` (free-text buyer note) — it is stored and
  never printed today. A real gap, but a **content** change, not a layout one;
  raised as a follow-up rather than smuggled in here.
- Printing the corrected invoice's NUMBER on a credit note (Art. 219). The
  renderer receives `corrected_invoice_id` but not the corrected invoice; wiring
  a DB read into a pure renderer is a signature change and belongs in its own
  order.
- `app/services/report_writers.py` and `expenses.build_pdf` — different
  documents, not part of this design.
- The SPA. No frontend file changes.

## Files to touch

| File | Change |
|---|---|
| `backend/app/services/invoice_pdf.py` | Rewrite `build_pdf`'s layout; add `_NumberedCanvas`, `_page_furniture`; keep the signature, the Factur-X conditional and `PdfUnavailable` |
| `backend/tests/test_invoice_pdf_layout.py` | **new** — layout/structure characterisation over the real renderer |
| `docs/design/invoice-layout.md` | **new** — the design record: three options, the choice, the reasons, what survives |
| `TODO.md` | board row for WO-97 |

> Verified present: `invoice_pdf.py`, `TODO.md`. `docs/design/` does not exist yet
> and is created by this order.

## Implementation guidance

1. **Characterise first.** `tests/test_invoice_pdf_facturx_claim.py` and
   `tests/test_issued_multi_issuer.py::test_payment_instructions_render_on_pdf` /
   `::test_pdf_content_matches_stored_invoice_values` are the existing behavioural
   net over this renderer. Run them green before touching anything; they must
   stay green **unmodified**.
2. **Palette.** Retire `_BRAND` as a *structural* colour. Ink `#14181C`, muted
   `#5C6670`, rule `#C9CFD4`. Nothing carries meaning by colour alone; the
   document must read identically in greyscale.
3. **Header.** One two-column table: left = optional logo (≤38×14mm,
   `kind="proportional"`) above the seller block (bold legal name, address lines,
   then `VAT … · Reg …` on one line); right = `INVOICE` / `CREDIT NOTE` at 26pt
   bold, right-aligned. The logo sits **above** the seller identity, never in
   place of it.
4. **Metadata strip.** Four labelled columns — `BILL TO`, `INVOICE NO.` (or
   `CREDIT NOTE NO.`), `ISSUED`, `DUE`. Labels 7.5pt uppercase muted with
   `charSpace` tracking, above a 0.5pt hairline; values below. The `DUE` column is
   dropped and its width redistributed when `due_date` is absent, rather than
   printing an empty labelled column.
5. **Quiet line.** `Supply date … · Purchase order … · Terms …`, each part
   omitted when absent, the whole line omitted when all parts are.
6. **Line table.** Description / Qty / Unit price / [Disc %] / VAT / Net. Header
   labels in the strip's small-uppercase style; 0.6pt ink rule under the header,
   0.3pt hairlines between rows and **none under the last**; numeric columns
   right-aligned; `repeatRows=1`. Every cell is a `Paragraph` so long
   descriptions wrap instead of overflowing.
7. **Totals.** Right-aligned two-column block: `Subtotal`, then one row per
   `RateBucket` reading `VAT {rate}% of {base}` → `{vat}` (this is what makes the
   multi-rate case correct **and** keeps the single-rate case reading cleanly),
   then `Total due` set apart by a 0.6pt rule above and 12pt bold on both label
   and value. Wrapped with the exemption note in a `KeepTogether`.
8. **Money.** Unchanged: `_money` formats `Decimal` with thousands separators, two
   decimals, ROUND-preserving — no float ever enters the renderer, and the
   renderer computes nothing. Every amount is suffixed with the invoice currency,
   so no figure on the page is ambiguous about its unit.
9. **Page furniture.** A `_NumberedCanvas` buffering pages so `Page n of m` is
   **real** (two-pass: `showPage` defers, `save` knows the count). Drawn only when
   `m > 1`, alongside the document number. A decorative "Page 1" on a one-page
   invoice is noise; a wrong "of m" is a lie.
10. **Fail-open gates.** The logo render stays inside `try/except` with
    `log.warning` — a corrupt logo must never cost the customer their invoice.
    That is the only fail-open in this module and it is preserved verbatim.

## Invariants this order must preserve

- **§4.9 Decimal money.** The renderer receives `Decimal`s and formats them. No
  arithmetic is added; `Decimal(v)` conversion only. `tests/test_money_invariants.py`
  continues to scan this path.
- **§4.10 the server recomputes every total.** Unchanged — the PDF prints
  `vat.VatResult`, which `vat.compute` produced; the renderer derives nothing.
- **§4.18 an issued document is immutable.** The layout change alters how an
  invoice is *drawn*, never what it *says*. Re-rendering a previously issued
  invoice yields the same figures, number, dates and parties.
- **§4.20 the wire contract.** `build_pdf`'s signature and its
  `PdfUnavailable` failure mode are untouched; `issued.py:904` calls it
  identically. `Content-Type` and `Content-Disposition` are unchanged.
- **§4.19 AI.** None involved.
- **Tenancy.** The renderer takes an already-tenant-scoped invoice object and a
  frozen `seller_json`; it opens no session and issues no query. Unchanged.

## Database / migration impact

**None.** No column is added, altered or read that was not already available on
the objects passed in. `supply_date` already exists on `issued_invoices`
(`app/models/issued_invoice.py:126`) and is simply printed for the first time.

## Testing requirements

New — `backend/tests/test_invoice_pdf_layout.py`:

- `test_metadata_strip_labels_are_present` — `BILL TO`, `INVOICE NO.`, `ISSUED`,
  `DUE` all extractable.
- `test_credit_note_heading_and_number_label` — `CREDIT NOTE` heading and
  `CREDIT NOTE NO.` label; the word `INVOICE` does not head the document.
- `test_due_column_is_omitted_when_there_is_no_due_date` — `DUE` absent rather
  than labelled and empty.
- `test_every_vat_rate_gets_its_own_breakdown_row` — three rates in, three
  `VAT n% of base` rows out, and their bases sum to the subtotal.
- `test_single_rate_still_shows_base_and_vat` — the common case reads correctly.
- `test_zero_vat_scheme_prints_the_scheme_note_and_zero_vat` — reverse charge.
- `test_explicit_exemption_reason_wins_over_the_scheme_note` — BT-120 precedence.
- `test_exemption_reason_is_printed_even_on_a_standard_scheme` — the legally
  required text can never be dropped because the scheme looks ordinary.
- `test_thirty_lines_span_pages_with_a_repeated_header` — >1 page, the
  `DESCRIPTION` label appears once per page.
- `test_page_numbering_is_real_on_a_multipage_invoice` — `Page 1 of 2` **and**
  `Page 2 of 2` present, and `of 2` matches the actual page count.
- `test_single_page_invoice_has_no_page_furniture` — no `Page 1 of 1`.
- `test_totals_block_is_not_orphaned_from_the_table` — on a line count tuned to
  the break, the last line item and `Total due` land on the same page.
- `test_discount_column_appears_only_when_a_line_carries_a_discount`.
- `test_supply_date_is_printed_only_when_it_differs_from_the_issue_date`.
- `test_seven_figure_and_negative_amounts_render_intact` — `1,234,567.89` and a
  negative line survive extraction with their separators and sign.
- `test_long_buyer_name_and_description_do_not_truncate`.
- `test_logo_is_rendered_and_a_corrupt_logo_does_not_break_the_invoice` —
  fail-open proven, not asserted by comment.

Unmodified and still green: `tests/test_invoice_pdf_facturx_claim.py` (all five),
`tests/test_issued_multi_issuer.py` (PDF pair), `tests/test_credit_notes.py`,
`tests/test_issued_line_fields.py`.

Authorization / cross-tenant / concurrency cases: **not applicable** — this order
changes a pure rendering function that performs no I/O, holds no session and
takes no id. The tenant boundary for the PDF lives in `issued.py::_load` and is
already covered by `tests/test_cross_tenant_isolation.py`; it is untouched.

**Visual verification is mandatory and is the real gate.** A layout regression is
invisible to text extraction — every assertion above passes on a page whose
columns collide. Each case is rendered, rasterised with `pypdfium2` and
**looked at**: short invoice, 30-line invoice across pages, three VAT rates,
credit note, exemption reason, reverse charge, with-logo, seven-figure amount,
negative amount, long names, no-due-date.

## Acceptance criteria (verifiable checklist)

- [ ] `python -m pytest tests/test_invoice_pdf_facturx_claim.py -q` passes with
      the file **byte-identical** to `c1e5ee8` (`git diff --stat` shows it untouched).
- [ ] A 30-line invoice renders `> 1` page; `DESCRIPTION` appears once per page;
      `Page 1 of 2` and `Page 2 of 2` are both extractable and `2` equals
      `len(PdfReader(...).pages)`.
- [ ] A single-page invoice contains no `Page 1 of 1`.
- [ ] An invoice with rates 21/9/0 produces three rows matching
      `VAT \d+% of ` and the three bases sum to the printed subtotal.
- [ ] `doc_type="credit_note"` renders the heading `CREDIT NOTE` and the label
      `CREDIT NOTE NO.`.
- [ ] `tax_exemption_reason="Custom BT-120 ground"` appears in the extracted text
      even when `vat_scheme="standard"`.
- [ ] A line with `discount_percent=0` on every line produces no `DISC %` header;
      one non-zero line produces it.
- [ ] `supply_date == issue_date` → no `Supply date` text; a differing
      `supply_date` → present.
- [ ] `logo=("image/png", b"not a png")` still returns a valid PDF containing the
      invoice number (fail-open), and logs a warning.
- [ ] `ruff check app tests`, `ruff format --check app tests`, `mypy app` all clean.
- [ ] Full backend suite: `2403 passed / 10 skipped` before, and after
      `2403 + <new count> passed / 10 skipped`, with zero pre-existing tests
      modified except any listed in the report with its justification.
- [ ] `python scripts/pii_scan.py --tree` clean.

## Rollback strategy

Pure code revert of one module — `git revert` the layout commit. No migration, no
data, no one-way effect. Previously issued invoices are **not** stored as PDFs;
`/issued/{id}/pdf` re-renders on demand from `seller_json` + the stored lines, so
a revert restores the old appearance for every invoice, historical ones included,
with no reconciliation needed. The narrow mitigation short of a full revert
(should only the page furniture misbehave) is to drop the `canvasmaker=` argument
from the `doc.build(...)` call — one line, and the document renders exactly as it
otherwise would, minus the footer.

## Documentation to update

- `docs/design/invoice-layout.md` — **new**; the design record.
- `TODO.md` — the board row.
- No ADR is contradicted. `docs/architecture/*` describes no invoice layout;
  the module docstring in `invoice_pdf.py` remains accurate (still a functional
  embedded-XML hybrid, still not strict PDF/A-3) and is extended, not corrected.

## Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/test_invoice_pdf_layout.py tests/test_invoice_pdf_facturx_claim.py \
                 tests/test_issued_multi_issuer.py tests/test_credit_notes.py \
                 tests/test_issued_line_fields.py -q
git diff --stat c1e5ee8 -- tests/test_invoice_pdf_facturx_claim.py   # must be EMPTY
python -m pytest -q                                                  # full baseline
cd /home/user/Bid_it && python scripts/pii_scan.py --tree
# and the gate that text extraction cannot give you — render and LOOK:
python /tmp/.../render_cases.py   # 11 cases → PNG, each one viewed
```
