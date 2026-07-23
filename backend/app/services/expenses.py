"""Employee expense reports — totals, workflow helpers, and a PDF export.

Workflow: draft → submitted → approved | rejected → reimbursed.
Ownership: an employee owns their reports; an owner (manager) approves/reimburses
and can see the whole tenant's reports. Tenant isolation is handled by the ORM
guard; this layer adds the per-employee visibility rule.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal

from app.core.money import q2 as q
from app.models.expense import ExpenseItem, ExpenseReport


def compute_totals(items: list) -> tuple[Decimal, Decimal]:
    total = q(sum((Decimal(i.amount) for i in items), start=Decimal("0")))
    vat = q(sum((Decimal(i.vat_amount) for i in items), start=Decimal("0")))
    return total, vat


def derive_amount(payload) -> Decimal:
    """The gross amount in the reporting currency. For mileage/per-diem it is
    computed from the inputs; for a foreign-currency item with an FX rate and no
    explicit amount it is original × rate; otherwise the supplied `amount`."""
    etype = getattr(payload, "expense_type", "standard")
    amount = Decimal(getattr(payload, "amount", 0) or 0)
    if etype == "mileage" and amount == 0:
        dist = Decimal(getattr(payload, "mileage_distance", 0) or 0)
        rate = Decimal(getattr(payload, "mileage_rate", 0) or 0)
        amount = dist * rate
    elif etype == "per_diem" and amount == 0:
        days = Decimal(getattr(payload, "per_diem_days", 0) or 0)
        rate = Decimal(getattr(payload, "per_diem_rate", 0) or 0)
        amount = days * rate
    elif amount == 0:
        orig = getattr(payload, "original_amount", None)
        fx = getattr(payload, "fx_rate", None)
        if orig is not None and fx is not None:
            amount = Decimal(orig) * Decimal(fx)
    return q(amount)


def item_from(payload) -> ExpenseItem:
    from app.core.dimensions import DIMENSION_KEYS

    return ExpenseItem(
        spend_date=payload.spend_date,
        category=payload.category,
        description=payload.description,
        merchant=payload.merchant,
        amount=derive_amount(payload),
        currency=(c.upper() if (c := getattr(payload, "currency", None)) else None),
        original_amount=(
            q(o) if (o := getattr(payload, "original_amount", None)) is not None else None
        ),
        fx_rate=getattr(payload, "fx_rate", None),
        fx_source=getattr(payload, "fx_source", None),
        vat_amount=q(payload.vat_amount),
        reclaimable_tax=getattr(payload, "reclaimable_tax", True),
        payment_method=payload.payment_method,
        customer_billable=getattr(payload, "customer_billable", False),
        billable_customer=getattr(payload, "billable_customer", None),
        comment=getattr(payload, "comment", None),
        missing_receipt_declaration=getattr(payload, "missing_receipt_declaration", None),
        expense_type=getattr(payload, "expense_type", "standard"),
        mileage_distance=getattr(payload, "mileage_distance", None),
        mileage_rate=getattr(payload, "mileage_rate", None),
        mileage_unit=getattr(payload, "mileage_unit", None),
        per_diem_days=getattr(payload, "per_diem_days", None),
        per_diem_rate=(
            q(r) if (r := getattr(payload, "per_diem_rate", None)) is not None else None
        ),
        **{k: getattr(payload, k, None) for k in DIMENSION_KEYS},
    )


def now() -> datetime:
    return datetime.now(UTC)


def item_missing(item: ExpenseItem) -> list[str]:
    """Compliance requirements every expense entry must carry before submission:
    a business-purpose comment and a receipt. Mileage/per-diem entries are computed
    allowances with no receipt; a written missing-receipt declaration also
    substitutes for the document (an explicit affidavit in its place)."""
    missing = []
    if not (item.comment and item.comment.strip()):
        missing.append("business purpose")
    receipt_exempt = item.expense_type in ("mileage", "per_diem") or bool(
        item.missing_receipt_declaration and item.missing_receipt_declaration.strip()
    )
    if item.receipt_sha256 is None and not receipt_exempt:
        missing.append("receipt or a missing-receipt declaration")
    return missing


def incomplete_items(report: ExpenseReport) -> list[tuple[ExpenseItem, list[str]]]:
    """Every item that is not yet submission-ready, with what it's missing."""
    return [(it, m) for it in report.items if (m := item_missing(it))]


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
    body = ParagraphStyle(
        "b", parent=styles["Normal"], fontSize=9.5, leading=12, textColor=colors.HexColor(ink)
    )
    small = ParagraphStyle(
        "s", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor(muted)
    )
    title = ParagraphStyle(
        "t", parent=styles["Title"], fontSize=20, textColor=colors.HexColor(brand)
    )

    def money(v):
        return f"{Decimal(v):,.2f} {ccy}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=f"Expense report {report.title}",
    )
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
        rows.append(
            [
                str(it.spend_date),
                it.category,
                Paragraph(it.description, body),
                it.merchant or "—",
                money(it.vat_amount),
                money(it.amount),
            ]
        )
    tbl = Table(
        rows, colWidths=[22 * mm, 24 * mm, 58 * mm, 30 * mm, 20 * mm, 24 * mm], repeatRows=1
    )
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(brand)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(line)),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [tbl, Spacer(1, 10)]

    totals = Table(
        [
            ["Reclaimable VAT", money(report.vat_total)],
            ["Total", money(report.total)],
        ],
        colWidths=[40 * mm, 40 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("LINEABOVE", (0, 1), (-1, 1), 0.6, colors.HexColor(ink)),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor(brand)),
            ]
        )
    )
    story += [totals]
    if report.total_eur is not None and ccy != "EUR":
        story += [
            Spacer(1, 4),
            Paragraph(f"Total in EUR (ECB): {Decimal(report.total_eur):,.2f} EUR", small),
        ]
    if report.decided_by:
        story += [
            Spacer(1, 10),
            Paragraph(
                f"{report.status.title()} by {report.decided_by}"
                + (f" — {report.decision_note}" if report.decision_note else ""),
                small,
            ),
        ]

    doc.build(story)
    return buf.getvalue()
