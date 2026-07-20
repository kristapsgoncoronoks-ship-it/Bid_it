"""Employee expense reports — totals, workflow helpers, and a PDF export.

Workflow: draft → submitted → approved | rejected → reimbursed.
Ownership: an employee owns their reports; an owner (manager) approves/reimburses
and can see the whole tenant's reports. Tenant isolation is handled by the ORM
guard; this layer adds the per-employee visibility rule.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.money import q2 as q
from app.models.expense import ExpenseItem, ExpenseReport


def compute_totals(items: list) -> tuple[Decimal, Decimal]:
    total = q(sum((Decimal(i.amount) for i in items), start=Decimal("0")))
    vat = q(sum((Decimal(i.vat_amount) for i in items), start=Decimal("0")))
    return total, vat


def item_from(payload) -> ExpenseItem:
    return ExpenseItem(
        spend_date=payload.spend_date,
        category=payload.category,
        description=payload.description,
        merchant=payload.merchant,
        amount=q(payload.amount),
        vat_amount=q(payload.vat_amount),
        payment_method=payload.payment_method,
        comment=getattr(payload, "comment", None),
    )


def now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# PDF export
# --------------------------------------------------------------------------- #
class PdfUnavailable(RuntimeError):
    pass


def build_pdf(report: ExpenseReport) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as e:  # pragma: no cover
        raise PdfUnavailable(str(e))

    brand, ink, muted, line = "#2f57d4", "#1e293b", "#64748b", "#e2e8f0"
    ccy = report.currency
    styles = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=styles["Normal"], fontSize=9.5, leading=12, textColor=colors.HexColor(ink))
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor(muted))
    title = ParagraphStyle("t", parent=styles["Title"], fontSize=20, textColor=colors.HexColor(brand))

    def money(v):
        return f"{Decimal(v):,.2f} {ccy}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm, title=f"Expense report {report.title}")
    story = [
        Paragraph("EXPENSE REPORT", title),
        Paragraph(f"<b>{report.title}</b>", body),
        Paragraph(
            f"Employee: {report.employee_name} &nbsp;·&nbsp; Status: {report.status} &nbsp;·&nbsp; "
            f"Submitted: {report.submitted_at.date() if report.submitted_at else '—'}",
            small,
        ),
        Spacer(1, 12),
    ]

    rows = [["Date", "Category", "Description", "Merchant", "VAT", "Amount"]]
    for it in report.items:
        rows.append([
            str(it.spend_date), it.category, Paragraph(it.description, body),
            it.merchant or "—", money(it.vat_amount), money(it.amount),
        ])
    tbl = Table(rows, colWidths=[22 * mm, 24 * mm, 58 * mm, 30 * mm, 20 * mm, 24 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(brand)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(line)),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [tbl, Spacer(1, 10)]

    totals = Table([
        ["Reclaimable VAT", money(report.vat_total)],
        ["Total", money(report.total)],
    ], colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("LINEABOVE", (0, 1), (-1, 1), 0.6, colors.HexColor(ink)),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"), ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor(brand)),
    ]))
    story += [totals]
    if report.total_eur is not None and ccy != "EUR":
        story += [Spacer(1, 4), Paragraph(f"Total in EUR (ECB): {Decimal(report.total_eur):,.2f} EUR", small)]
    if report.decided_by:
        story += [Spacer(1, 10), Paragraph(
            f"{report.status.title()} by {report.decided_by}"
            + (f" — {report.decision_note}" if report.decision_note else ""), small)]

    doc.build(story)
    return buf.getvalue()
