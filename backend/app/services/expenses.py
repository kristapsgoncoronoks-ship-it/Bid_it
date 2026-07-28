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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.money import q2 as q
from app.models.expense import ExpenseItem, ExpenseReport
from app.models.fx import FxSource
from app.services import fx as fx_service

# Reports whose figures aren't final: a draft can still change, a rejected report
# was never approved for reimbursement. Neither should count toward "cash we can
# reclaim" (C1.8).
_NOT_YET_RECLAIMABLE_STATUSES = ("draft", "rejected")

# Precision for a stored/implied FX rate (matches the Numeric(18, 8) column).
_RATE_EXP = Decimal("0.00000001")


def compute_totals(items: list) -> tuple[Decimal, Decimal]:
    total = q(sum((Decimal(i.amount) for i in items), start=Decimal("0")))
    vat = q(sum((Decimal(i.vat_amount) for i in items), start=Decimal("0")))
    return total, vat


def reclaimable_vat_of(items: list) -> Decimal:
    """The subset of `vat_total` that is actually reclaimable (ADR-0029/C1.8):
    only items with `reclaimable_tax` set — some categories/countries don't allow
    reclaiming VAT even though it was paid. Distinct from `compute_totals`'s `vat`,
    which is the total tax paid on the report regardless of reclaimability."""
    return q(sum((Decimal(i.vat_amount) for i in items if i.reclaimable_tax), start=Decimal("0")))


async def reclaimable_vat_total(db: AsyncSession, org_id: str, employee_id: str) -> Decimal:
    """An employee's total reclaimable VAT across their reports (the `/summary`
    KPI, ADR-0029/C1.8): only `reclaimable_tax` items, and only on reports that
    have left draft/rejected — a draft's figures aren't final and a rejected
    report was never approved, so neither belongs in "cash we can reclaim"."""
    total = await db.scalar(
        select(func.coalesce(func.sum(ExpenseItem.vat_amount), 0))
        .join(ExpenseReport, ExpenseReport.id == ExpenseItem.report_id)
        .where(
            ExpenseReport.org_id == org_id,
            ExpenseReport.employee_id == employee_id,
            ExpenseReport.status.notin_(_NOT_YET_RECLAIMABLE_STATUSES),
            ExpenseItem.reclaimable_tax.is_(True),
        )
    )
    return q(total or Decimal("0"))


def derive_amount(payload) -> Decimal:
    """The gross amount in the reporting currency. Mileage/per-diem entries are
    computed from their inputs (distance × per-km, days × per-day — allowance
    tariffs, not FX rates); otherwise the supplied `amount` stands. A foreign
    original-currency figure is converted separately by `apply_item_fx` under the
    single ECB convention (WO-8) — the old original × rate multiply is gone."""
    etype = getattr(payload, "expense_type", "standard")
    amount = Decimal(getattr(payload, "amount", 0) or 0)
    if etype == "mileage" and amount == 0:
        dist = Decimal(getattr(payload, "mileage_distance", 0) or 0)
        per_km = Decimal(getattr(payload, "mileage_rate", 0) or 0)
        amount = dist * per_km
    elif etype == "per_diem" and amount == 0:
        days = Decimal(getattr(payload, "per_diem_days", 0) or 0)
        per_day = Decimal(getattr(payload, "per_diem_rate", 0) or 0)
        amount = days * per_day
    return q(amount)


async def apply_item_fx(db: AsyncSession, item: ExpenseItem, report_currency: str) -> None:
    """Resolve an entry's original-currency figure into the report currency and
    stamp its provenance, under the ONE FX convention (WO-8): a rate is original-
    currency units per 1 unit of the report currency, and converting DIVIDES
    (for an EUR report that is exactly the ECB convention — units per 1 EUR).

    Provenance is server-derived, never trusted from the client (§4.10 — the
    server recomputes): a claimant-typed converted amount is `stated` (the card
    statement is the observable fact; the implied rate is recorded from it), an
    ECB conversion is `ecb` with the resolved rate recorded.

    Fail-CLOSED: an entry that cannot be converted is REFUSED at write with a
    422 and a stable code — a silently-zero or guessed amount would flow into
    the report total, the reimbursement batch and ultimately a bank file.
    """
    report_ccy = (report_currency or "EUR").upper()
    ccy = item.currency.upper() if item.currency else None
    item.currency = ccy
    if item.original_amount is None or ccy is None:
        # Not a foreign-currency entry — provenance fields are meaningless noise;
        # normalise them away rather than storing an unverifiable claim.
        item.fx_source = None
        item.fx_rate = None
        return
    original = Decimal(item.original_amount)
    if ccy == report_ccy:
        # Same currency: identity. `eur` provenance only when that currency IS
        # the euro; otherwise there is no EUR claim to stamp.
        if not item.amount:
            item.amount = q(original)
        item.fx_source = FxSource.eur.value if ccy == "EUR" else None
        item.fx_rate = None
        return
    if item.amount:
        # The claimant stated the converted amount (their card/bank statement).
        # Record the implied rate so the conversion is fully documented.
        item.fx_source = FxSource.stated.value
        item.fx_rate = (original / Decimal(item.amount)).quantize(_RATE_EXP)
        return
    if item.fx_rate:
        # A stated rate: original units per 1 report-currency unit → divide.
        item.amount = q(original / Decimal(item.fx_rate))
        item.fx_source = FxSource.stated.value
        return
    if report_ccy == "EUR":
        eur, resolved = await fx_service.to_eur(db, original, ccy, item.spend_date)
        if eur is None or resolved is None:
            raise AppError(
                f"No exchange rate is available for {ccy} on {item.spend_date} — "
                "enter the converted amount (or a rate), or refresh the ECB rates.",
                code="fx_rate_unavailable",
                status=422,
            )
        item.amount = eur
        item.fx_source = FxSource.ecb.value
        item.fx_rate = Decimal(resolved.rate).quantize(_RATE_EXP)
        return
    # Foreign entry on a non-EUR report with nothing stated: refusing beats
    # inventing a cross rate the claimant never saw (fail-closed, §4.14).
    raise AppError(
        f"Cannot convert {ccy} into {report_ccy} automatically — enter the "
        "converted amount or a rate (original units per 1 " + report_ccy + ").",
        code="fx_cross_currency_unsupported",
        status=422,
    )


async def build_items(db: AsyncSession, payloads: list, report_currency: str) -> list[ExpenseItem]:
    """Build ORM items from request payloads, resolving each foreign-currency
    figure through the single conversion path (`apply_item_fx`)."""
    items = []
    for p in payloads:
        item = item_from(p)
        await apply_item_fx(db, item, report_currency)
        items.append(item)
    return items


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
            ["VAT", money(report.vat_total)],
            ["Reclaimable VAT", money(reclaimable_vat_of(report.items))],
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
                ("LINEABOVE", (0, 2), (-1, 2), 0.6, colors.HexColor(ink)),
                ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 2), (-1, 2), colors.HexColor(brand)),
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
