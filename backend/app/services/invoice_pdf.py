"""Render an issued invoice as a polished PDF with the Factur-X XML embedded.

reportlab draws the human-readable invoice; pypdf attaches `factur-x.xml` so the
result is a hybrid e-invoice (readable by people and by machines — including our
own `einvoice.extract_embedded_xml`). Not a strict PDF/A-3 (colour profile / XMP
conformance is a hardening step), but a functional embedded-XML hybrid.

Layout: "refined ledger" (WO-97, design record `docs/design/invoice-layout.md`).
Seller identity and the document word at the top, a labelled metadata strip
(bill-to / number / issued / due), the line table, a right-aligned totals block
whose VAT breakdown carries one row PER RATE, then the legal note and payment
details.

Two rules govern everything below, because this page is the only artefact of the
product a customer's customer ever sees:

* **No meaning is carried by colour alone.** Structure comes from rules, weight
  and space, so the document reads identically on a mono laser or a fax.
* **Nothing here computes.** The renderer formats `Decimal`s that
  `vat.compute` already produced. It adds no arithmetic and rounds nothing.
"""

from __future__ import annotations

import io
import logging
from datetime import date
from decimal import Decimal

from app.services.vat import SCHEME_NOTES, VatResult

log = logging.getLogger("invoiceiq.invoice_pdf")

# One ink, one muted, one rule. `_BRAND` is deliberately gone from the layout:
# it used to fill the line-table header and stripe the rows, which is exactly
# the structure-by-colour a photocopier destroys.
_INK = "#14181C"
_MUTED = "#5C6670"
_RULE = "#C9CFD4"

_MARGIN_MM = 18.0
_CONTENT_MM = 210.0 - 2 * _MARGIN_MM  # A4 width less both margins = 174mm


class PdfUnavailable(RuntimeError):
    pass


def _money(v, ccy: str) -> str:
    """Thousands-separated, two decimals, currency suffixed.

    Every amount on the page states its currency, so no figure is ambiguous
    about its unit — including on a page that has been separated from page 1.
    """
    return f"{Decimal(v):,.2f} {ccy}"


def _num(v) -> str:
    """A quantity / rate / percentage, plainly.

    Not `:g`: `format(Decimal("1000000"), "g")` is `1E+6`, so a quantity of a
    million litres would print as scientific notation on an invoice. `:,f` never
    goes exponential; the trailing-zero strip keeps `1.50` reading as `1.5` and
    `4` as `4`.
    """
    s = f"{Decimal(str(v)):,f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _date(v) -> str:
    """`31 January 2026`. Strings (and None) pass through untouched."""
    if v is None:
        return ""
    if isinstance(v, date):
        return f"{v.day} {v.strftime('%B %Y')}"
    return str(v)


def _join(parts, sep: str = " · ") -> str:
    return sep.join([p for p in parts if p])


def build_pdf(
    invoice, seller: dict, vat: VatResult, xml_bytes: bytes, logo: tuple[str, bytes] | None
) -> bytes:
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as pdfcanvas
        from reportlab.platypus import (
            BaseDocTemplate,
            Frame,
            Image,
            KeepTogether,
            PageTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as e:  # pragma: no cover
        raise PdfUnavailable(str(e))

    ink = colors.HexColor(_INK)
    muted = colors.HexColor(_MUTED)
    rule = colors.HexColor(_RULE)
    ccy = invoice.currency
    ss = getSampleStyleSheet()

    def style(name: str, size: float, leading: float, **kw) -> ParagraphStyle:
        bold = kw.pop("bold", False)
        return ParagraphStyle(
            name,
            parent=ss["Normal"],
            fontName="Helvetica-Bold" if bold else "Helvetica",
            fontSize=size,
            leading=leading,
            textColor=kw.pop("colour", ink),
            alignment=kw.pop("align", 0),
            **kw,
        )

    # 7.5pt uppercase, tracked, muted — the one label voice, used by the metadata
    # strip AND the table header so the two read as the same system.
    lbl = style("lbl", 7.5, 9.5, colour=muted, charSpace=0.6)
    body = style("body", 9.5, 13)
    bold = style("bold", 9.5, 13, bold=True)
    # Numeric cells are right-aligned IN THE PARAGRAPH, not by the table's ALIGN:
    # a cell holding a Paragraph ignores ALIGN (it aligns the paragraph's box,
    # which already fills the cell), which is why the prototype's figures came
    # out flush left. Right-aligned is what stacks the decimal points.
    num = style("num", 9.5, 13, align=2)
    lbl_r = style("lbl_r", 7.5, 9.5, colour=muted, charSpace=0.6, align=2)
    quiet = style("quiet", 8.5, 11, colour=muted)
    total_l = style("total_l", 12, 15, bold=True)
    total_r = style("total_r", 12, 15, bold=True, align=2)

    is_credit = getattr(invoice, "doc_type", "invoice") == "credit_note"
    heading = "CREDIT NOTE" if is_credit else "INVOICE"
    number = str(getattr(invoice, "number", "") or "")

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        topMargin=_MARGIN_MM * mm,
        bottomMargin=_MARGIN_MM * mm,
        leftMargin=_MARGIN_MM * mm,
        rightMargin=_MARGIN_MM * mm,
        title=f"{heading.title()} {number}".strip(),
    )
    # A ZERO-PADDED frame, not `SimpleDocTemplate`'s default 6pt one. With the
    # default, a full-width table (which reportlab centres, overflowing the
    # padding symmetrically) starts 6pt to the LEFT of every paragraph placed
    # directly in the frame — so the payment block and the PO line sat 2mm
    # inboard of the table above them. Zero padding makes the 18mm margin the
    # single true left edge for tables and paragraphs alike.
    doc.addPageTemplates(
        [
            PageTemplate(
                id="invoice",
                frames=[
                    Frame(
                        doc.leftMargin,
                        doc.bottomMargin,
                        doc.width,
                        doc.height,
                        leftPadding=0,
                        rightPadding=0,
                        topPadding=0,
                        bottomPadding=0,
                        id="body",
                    )
                ],
            )
        ]
    )
    story: list = []

    # ── Header: who is issuing this, and what it is ───────────────────────────
    seller_txt = "<br/>".join(
        filter(
            None,
            [
                f"<b>{seller.get('legal_name') or ''}</b>",
                seller.get("address_line1"),
                seller.get("address_line2"),
                " ".join(filter(None, [seller.get("postal_code"), seller.get("city")])),
                seller.get("country") or "",
                _join(
                    [
                        f"VAT {seller['vat_number']}" if seller.get("vat_number") else "",
                        f"Reg {seller['registration_number']}"
                        if seller.get("registration_number")
                        else "",
                    ]
                ),
            ],
        )
    )

    ident: list = []
    if logo is not None:
        try:
            ident.append(
                Image(io.BytesIO(logo[1]), width=38 * mm, height=14 * mm, kind="proportional")
            )
            ident.append(Spacer(1, 6))
        except Exception:
            # FAIL OPEN, deliberately: a corrupt or unreadable logo must never
            # cost the customer their invoice. The document is legally complete
            # without a mark; it is not complete without the figures.
            log.warning("invoice logo render failed, omitting", exc_info=True)
    ident.append(Paragraph(seller_txt, body))

    head = Table(
        [[ident, Paragraph(heading, style("h1", 26, 28, bold=True, align=2))]],
        colWidths=[105 * mm, 69 * mm],
    )
    head.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story += [head, Spacer(1, 16)]

    # ── Metadata strip: bill-to, number, issued, due ──────────────────────────
    buyer_txt = "<br/>".join(
        filter(
            None,
            [
                f"<b>{invoice.buyer_name}</b>",
                invoice.buyer_address_line1,
                " ".join(filter(None, [invoice.buyer_postal_code, invoice.buyer_city])),
                invoice.buyer_country,
                f"VAT {invoice.buyer_vat_number}" if invoice.buyer_vat_number else None,
            ],
        )
    )
    due = _date(getattr(invoice, "due_date", None))
    labels = ["BILL TO", f"{heading} NO.", "ISSUED"]
    values = [
        Paragraph(buyer_txt, body),
        Paragraph(number or "—", bold),
        Paragraph(_date(invoice.issue_date), body),
    ]
    # An empty labelled column asks a reader to wonder what is missing. A due
    # date that does not exist (credit notes routinely have none) is better
    # omitted than printed blank, so the column is dropped and its width shared.
    widths = [76.0, 38.0, 30.0, 30.0]
    if due:
        labels.append("DUE")
        values.append(Paragraph(due, bold))
    else:
        widths = [86.0, 48.0, 40.0]

    meta = Table(
        [[Paragraph(t, lbl) for t in labels], values],
        colWidths=[w * mm for w in widths],
    )
    meta.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, rule),
                ("TOPPADDING", (0, 1), (-1, 1), 5),
            ]
        )
    )
    story += [meta, Spacer(1, 6)]

    # ── The quiet line: supply date (Art. 226(7)), PO reference, terms ────────
    supply = getattr(invoice, "supply_date", None)
    terms = ""
    if getattr(invoice, "due_date", None) and invoice.issue_date:
        try:
            days = (invoice.due_date - invoice.issue_date).days
            terms = f"Terms {days} days" if days >= 0 else ""
        except TypeError:  # non-date inputs (a caller passing strings)
            terms = ""
    aside = _join(
        [
            # Mandatory when it is determined and differs from the issue date.
            f"Supply date {_date(supply)}" if supply and supply != invoice.issue_date else "",
            f"Purchase order {invoice.po_reference}"
            if getattr(invoice, "po_reference", None)
            else "",
            terms,
        ]
    )
    if aside:
        story += [Paragraph(aside, quiet)]
    story += [Spacer(1, 14)]

    # ── Line table ────────────────────────────────────────────────────────────
    # The discount column exists only when a line actually carries a discount —
    # but when one does it is MANDATORY (Art. 226(8): discounts not included in
    # the unit price must be shown), so it is never suppressed for tidiness.
    show_disc = any(Decimal(li.get("discount_percent", 0) or 0) > 0 for li in vat.lines)
    heads = ["DESCRIPTION", "QTY", "UNIT PRICE"] + (["DISC %"] if show_disc else [])
    heads += ["VAT", "NET"]
    head_cells = [Paragraph(heads[0], lbl)] + [Paragraph(h, lbl_r) for h in heads[1:]]
    rows: list[list] = [head_cells]
    for li in vat.lines:
        row = [
            Paragraph(li["description"], body),
            Paragraph(_num(li["quantity"]), num),
            Paragraph(_money(li["unit_price"], ccy), num),
        ]
        if show_disc:
            row.append(Paragraph(f"{_num(li.get('discount_percent', 0) or 0)}%", num))
        row += [
            Paragraph(f"{_num(li['vat_rate'])}%", num),
            Paragraph(_money(li["net_amount"], ccy), num),
        ]
        rows.append(row)

    # Widths are generous where the text is (description) and only as wide as the
    # longest plausible figure elsewhere: right-aligned numerals hug the column
    # edge, so a narrow numeric column reads as a clean decimal stack rather than
    # a lonely digit adrift in white space.
    # The numeric columns are sized for the WIDEST figure the schema allows —
    # `Numeric(14,2)`, i.e. `1,234,567.89 EUR` — not for the typical one. At a
    # narrower setting a seven-figure unit price wrapped onto a second line and
    # a 9,481,220-litre quantity broke mid-thousands ("9,481," / "220"), which
    # is how a document that looks fine in testing embarrasses someone in front
    # of their customer.
    if show_disc:
        widths_mm = [60.0, 18.0, 32.0, 15.0, 11.0, 38.0]
    else:
        widths_mm = [73.0, 20.0, 34.0, 11.0, 36.0]
    # ── Totals: one row PER VAT RATE, then the total set apart ────────────────
    # `VAT 21% of 1,200.00 EUR → 252.00 EUR` states the taxable base AND the tax
    # per rate (Art. 226(8)/(10)), which is what a multi-rate invoice must show
    # and what a single-rate one still reads cleanly as.
    # Three columns, the first an empty gutter, so the block sits hard against the
    # right margin without relying on `hAlign` (which a nested table inside a
    # spanned cell does not honour — it silently rendered flush LEFT).
    tot_rows: list[list] = [
        ["", Paragraph("Subtotal", body), Paragraph(_money(vat.subtotal, ccy), num)]
    ]
    for b in vat.breakdown:
        tot_rows.append(
            [
                "",
                Paragraph(f"VAT {_num(b.rate)}% of {_money(b.base, ccy)}", body),
                Paragraph(_money(b.vat, ccy), num),
            ]
        )
    total_label = "Total credited" if is_credit else "Total due"
    tot_rows.append(
        ["", Paragraph(total_label, total_l), Paragraph(_money(vat.total, ccy), total_r)]
    )
    last = len(tot_rows) - 1
    totals = Table(tot_rows, colWidths=[68 * mm, 62 * mm, 44 * mm])
    totals.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEABOVE", (1, last), (-1, last), 0.6, ink),
                ("TOPPADDING", (0, last), (-1, last), 6),
                ("TOPPADDING", (0, 0), (-1, last - 1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
            ]
        )
    )

    # Prefer the invoice's own exemption reason (EN-16931 BT-120); fall back to
    # the scheme's default legal note. When VAT is not charged this text is the
    # legal ground for not charging it, so it travels WITH the totals — a reader
    # must never find the zero without the reason.
    note = getattr(invoice, "tax_exemption_reason", None) or SCHEME_NOTES.get(invoice.vat_scheme)
    tail: list = [totals]
    if note:
        tail += [Spacer(1, 10), Paragraph(f"<b>{note}</b>", style("note", 8.5, 11.5))]

    # The totals are the LAST ROW OF THE LINE TABLE, not a flowable after it.
    # A `KeepTogether` keeps the block from being split, but nothing binds it to
    # the table above — so a 30-line invoice put its last line at the foot of
    # page 2 and the entire totals block alone on page 3, which is precisely the
    # orphan that makes a reader hunt for the amount they owe. As a spanned table
    # row the totals travel WITH the line items, and `repeatRows=1` guarantees
    # that if they do start a fresh page they do so under the column header,
    # reading as a continuation rather than a stray fragment.
    rows.append([tail] + [""] * (len(widths_mm) - 1))
    last_line = len(rows) - 2  # index of the final line item

    tbl = Table(rows, colWidths=[w * mm for w in widths_mm], repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, ink),
                # Hairlines BETWEEN line items — never under the last one.
                ("LINEBELOW", (0, 1), (-1, last_line - 1), 0.3, rule),
                ("TOPPADDING", (0, 0), (-1, last_line), 6),
                ("BOTTOMPADDING", (0, 0), (-1, last_line), 6),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
                # Right-aligned numerals need no left gutter, and the default
                # 6pt each side is what forced a 9,481,220-litre quantity to
                # break mid-thousands. Reclaim it for the figures.
                ("LEFTPADDING", (1, 0), (-1, -1), 3),
                ("RIGHTPADDING", (1, 0), (-2, -1), 3),
                ("SPAN", (0, -1), (-1, -1)),
                ("TOPPADDING", (0, -1), (-1, -1), 12),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 0),
                ("RIGHTPADDING", (0, -1), (-1, -1), 0),
                # No page break between the FINAL LINE ITEM and the totals: if
                # the totals will not fit, the last line is carried onto the new
                # page with them, so the total is never shown on a page holding
                # none of what it totals. `NOSPLIT` (not `rowSplitRange`, which
                # reportlab drops from the continuation table after the first
                # split) is remapped across every split, so the guarantee holds
                # on page 5 as well as page 2.
                ("NOSPLIT", (0, last_line), (-1, -1)),
            ]
        )
    )
    story += [tbl, Spacer(1, 18)]

    # ── Payment details ───────────────────────────────────────────────────────
    # A credit note is NOT payable — it reduces what the buyer owes. The heading,
    # the seller's collection IBAN and `payment_instructions` (free text written
    # for invoices, typically "payment within N days") were printed on it
    # unchanged, so a document that owes the reader money asked them to pay it.
    # Only the reference wording had been made conditional, which made the rest
    # read as deliberate.
    #
    # On a credit note the block therefore states the one thing true of EVERY
    # credit note and stops. Whether this credit is offset against the account or
    # refunded to a bank account is not known at this layer, so it is not
    # guessed; the seller's own `notes` remain the place to say so.
    if is_credit:
        pay = ["<b>Credit</b>", "No payment is due on this document."]
        if number:
            pay.append(f"Please quote {number} as the reference.")
        seller_keys = ("email", "notes")
    else:
        pay = ["<b>Payment</b>"]
        if seller.get("iban"):
            pay.append(
                _join(
                    [f"IBAN {seller['iban']}", f"BIC {seller['bic']}" if seller.get("bic") else ""]
                )
            )
        if number:
            pay.append(f"Please quote {number} as the payment reference.")
        seller_keys = ("payment_instructions", "email", "notes")
    for key in seller_keys:
        if seller.get(key):
            pay.append(str(seller[key]))
    story += [KeepTogether([Paragraph("<br/>".join(pay), quiet)])]

    # The compliance line is a CLAIM ABOUT THIS DOCUMENT, so it is printed only
    # when the claim is true. `build_pdf` is a public entry point and its
    # `xml_bytes` can be empty; printing the sentence unconditionally would put
    # "Factur-X XML embedded" on a PDF carrying no XML — a false statement on a
    # financial document, and one a reader has no way to check. Production
    # (`issued._render_pdf`) always supplies real CII, so this is a latent case
    # rather than a live one, but the sentence should never be able to lie.
    if xml_bytes:
        story += [
            Spacer(1, 8),
            Paragraph("Invoice compliant with EN 16931 · Factur-X XML embedded.", quiet),
        ]

    # ── Page furniture ────────────────────────────────────────────────────────
    # "Page n of m" has to be TRUE, so the canvas buffers every page and only
    # writes them out once it knows the count. It is drawn only when there is
    # more than one page: on a single-sheet invoice "Page 1 of 1" is noise, and
    # on a multi-sheet one a detached page 2 is unidentifiable without it — so
    # the footer also repeats the document heading and number.
    foot = _join([heading.title(), number], " ")

    class _NumberedCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._buffered: list[dict] = []

        def showPage(self) -> None:
            self._buffered.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._buffered)
            for state in self._buffered:
                self.__dict__.update(state)
                if total > 1:
                    self.saveState()
                    self.setFont("Helvetica", 7.5)
                    self.setFillColor(muted)
                    self.drawString(_MARGIN_MM * mm, 11 * mm, foot)
                    self.drawRightString(
                        (_MARGIN_MM + _CONTENT_MM) * mm,
                        11 * mm,
                        f"Page {self._pageNumber} of {total}",
                    )
                    self.restoreState()
                super().showPage()
            super().save()

    doc.build(story, canvasmaker=_NumberedCanvas)
    pdf_bytes = buf.getvalue()

    # Embed the CII XML → hybrid Factur-X. An EMPTY attachment is worse than no
    # attachment: a consuming system finds `factur-x.xml`, parses it, and fails,
    # rather than simply seeing a plain PDF. So attach only what exists.
    if not xml_bytes:
        return pdf_bytes
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.add_attachment("factur-x.xml", xml_bytes)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
