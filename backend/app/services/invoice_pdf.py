"""Render an issued invoice as a polished PDF with the Factur-X XML embedded.

reportlab draws the human-readable invoice; pypdf attaches `factur-x.xml` so the
result is a hybrid e-invoice (readable by people and by machines — including our
own `einvoice.extract_embedded_xml`). Not a strict PDF/A-3 (colour profile / XMP
conformance is a hardening step), but a functional embedded-XML hybrid.
"""

from __future__ import annotations

import io
import logging
from decimal import Decimal

from app.services.vat import SCHEME_NOTES, VatResult

log = logging.getLogger("invoiceiq.invoice_pdf")

_BRAND = "#2f57d4"
_INK = "#1e293b"
_MUTED = "#64748b"
_LINE = "#e2e8f0"


class PdfUnavailable(RuntimeError):
    pass


def _money(v, ccy: str) -> str:
    return f"{Decimal(v):,.2f} {ccy}"


def build_pdf(
    invoice, seller: dict, vat: VatResult, xml_bytes: bytes, logo: tuple[str, bytes] | None
) -> bytes:
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Image,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as e:  # pragma: no cover
        raise PdfUnavailable(str(e))

    ccy = invoice.currency
    styles = getSampleStyleSheet()
    small = ParagraphStyle(
        "small",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor(_MUTED),
    )
    body = ParagraphStyle(
        "body", parent=styles["Normal"], fontSize=9.5, leading=12, textColor=colors.HexColor(_INK)
    )
    h_lbl = ParagraphStyle(
        "hlbl",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor(_MUTED),
        spaceAfter=1,
    )
    title = ParagraphStyle(
        "title", parent=styles["Title"], fontSize=22, textColor=colors.HexColor(_BRAND), alignment=2
    )

    is_credit = getattr(invoice, "doc_type", "invoice") == "credit_note"
    heading = "CREDIT NOTE" if is_credit else "INVOICE"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=f"{heading.title()} {invoice.number}",
    )
    story: list = []

    seller_lines = "<br/>".join(
        filter(
            None,
            [
                f"<b>{seller.get('legal_name') or ''}</b>",
                seller.get("address_line1"),
                seller.get("address_line2"),
                " ".join(filter(None, [seller.get("postal_code"), seller.get("city")])),
                (seller.get("country") or ""),
                f"VAT: {seller.get('vat_number')}" if seller.get("vat_number") else None,
                f"Reg: {seller.get('registration_number')}"
                if seller.get("registration_number")
                else None,
            ],
        )
    )

    meta = Table(
        [
            [Paragraph(heading, title)],
            [Paragraph(f"<b>{invoice.number}</b>", ParagraphStyle("n", parent=body, alignment=2))],
            [
                Paragraph(
                    f"Issue date: {invoice.issue_date}",
                    ParagraphStyle("d", parent=small, alignment=2),
                )
            ],
            [
                Paragraph(
                    f"Due date: {invoice.due_date}" if invoice.due_date else "",
                    ParagraphStyle("d2", parent=small, alignment=2),
                )
            ],
            [
                Paragraph(
                    f"PO: {invoice.po_reference}" if getattr(invoice, "po_reference", None) else "",
                    ParagraphStyle("po", parent=small, alignment=2),
                )
            ],
        ],
        colWidths=[70 * mm],
    )
    meta.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )

    left_cells: list = []
    if logo is not None:
        try:
            left_cells.append(
                Image(io.BytesIO(logo[1]), width=40 * mm, height=16 * mm, kind="proportional")
            )
            left_cells.append(Spacer(1, 4))
        except Exception:
            log.warning("invoice logo render failed, omitting", exc_info=True)
    left_cells.append(Paragraph(seller_lines, body))
    left = Table([[c] for c in left_cells], colWidths=[95 * mm])
    left.setStyle(
        TableStyle(
            [
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    header = Table([[left, meta]], colWidths=[100 * mm, 78 * mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 14)]

    buyer_lines = "<br/>".join(
        filter(
            None,
            [
                f"<b>{invoice.buyer_name}</b>",
                invoice.buyer_address_line1,
                " ".join(filter(None, [invoice.buyer_postal_code, invoice.buyer_city])),
                invoice.buyer_country,
                f"VAT: {invoice.buyer_vat_number}" if invoice.buyer_vat_number else None,
            ],
        )
    )
    bill = Table(
        [[Paragraph("BILL TO", h_lbl)], [Paragraph(buyer_lines, body)]], colWidths=[95 * mm]
    )
    bill.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [bill, Spacer(1, 12)]

    # Line items. Show a discount column only when a line actually carries one.
    show_disc = any(Decimal(li.get("discount_percent", 0) or 0) > 0 for li in vat.lines)
    header_row = ["#", "Description", "Qty", "Unit price"]
    if show_disc:
        header_row.append("Disc %")
    header_row += ["VAT %", "Net"]
    rows = [header_row]
    for i, li in enumerate(vat.lines, start=1):
        row = [
            str(i),
            Paragraph(li["description"], body),
            f"{Decimal(li['quantity']):g}",
            _money(li["unit_price"], ccy),
        ]
        if show_disc:
            row.append(f"{Decimal(li.get('discount_percent', 0) or 0):g}%")
        row += [f"{Decimal(li['vat_rate']):g}%", _money(li["net_amount"], ccy)]
        rows.append(row)
    col_widths = [8 * mm, 74 * mm, 14 * mm, 26 * mm]
    if show_disc:
        col_widths.append(14 * mm)
    col_widths += [14 * mm, 28 * mm]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_BRAND)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor(_LINE)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [tbl, Spacer(1, 10)]

    # Totals + VAT breakdown side by side
    vat_rows = [["VAT rate", "Base", "VAT"]]
    for b in vat.breakdown:
        vat_rows.append([f"{Decimal(b.rate):g}%", _money(b.base, ccy), _money(b.vat, ccy)])
    vat_tbl = Table(vat_rows, colWidths=[20 * mm, 28 * mm, 28 * mm])
    vat_tbl.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(_MUTED)),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor(_LINE)),
            ]
        )
    )

    totals = Table(
        [
            ["Subtotal", _money(vat.subtotal, ccy)],
            ["VAT", _money(vat.tax_total, ccy)],
            ["Total", _money(vat.total, ccy)],
        ],
        colWidths=[30 * mm, 40 * mm],
    )
    totals.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("LINEABOVE", (0, 2), (-1, 2), 0.6, colors.HexColor(_INK)),
                ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 2), (-1, 2), colors.HexColor(_BRAND)),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    summary = Table([[vat_tbl, totals]], colWidths=[90 * mm, 88 * mm])
    summary.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [summary, Spacer(1, 14)]

    # Prefer the invoice's own exemption reason (EN-16931 BT-120); fall back to the
    # scheme's default legal note.
    note = getattr(invoice, "tax_exemption_reason", None) or SCHEME_NOTES.get(invoice.vat_scheme)
    if note:
        story += [Paragraph(f"<b>{note}</b>", small), Spacer(1, 6)]

    pay = []
    if seller.get("iban"):
        pay.append(
            f"IBAN: {seller['iban']}" + (f"  ·  BIC: {seller['bic']}" if seller.get("bic") else "")
        )
    if seller.get("payment_instructions"):
        pay.append(seller["payment_instructions"])
    if seller.get("email"):
        pay.append(f"Contact: {seller['email']}")
    if seller.get("notes"):
        pay.append(seller["notes"])
    if pay:
        story += [Paragraph("<br/>".join(pay), small)]
    story += [
        Spacer(1, 8),
        Paragraph("Invoice compliant with EN 16931 · Factur-X XML embedded.", small),
    ]

    doc.build(story)
    pdf_bytes = buf.getvalue()

    # Embed the CII XML → hybrid Factur-X.
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.add_attachment("factur-x.xml", xml_bytes)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
