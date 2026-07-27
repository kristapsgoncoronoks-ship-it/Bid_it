"""Read-only spend analytics over a single tenant's invoices.

Every query is grouped/aggregated in the database (not in Python) so it stays
fast as invoice volume grows. All functions take an explicit `org_id` and an
optional `[start, end]` issue-date window.

C1.7/WO-24: every function here sums a money column, so every function is
scoped to a SINGLE currency — the AR reports' pattern
(`issued_reports._pick_currency`): an explicit `currency` filter wins, else the
tenant's most-used currency; every other currency present is surfaced via
`available_currencies`, never folded into the same total (§4.14). A count
(`invoice_count`, `vendor_count`) is not a money sum and is not currency-scoped
by this rule — only amounts are (mirrors `ap_aging.DueSummary`'s docstring).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dimensions import DIMENSIONS, UNASSIGNED, is_dimension
from app.models.invoice import Invoice, InvoiceStatus, LineItem
from app.models.vendor import Vendor
from app.schemas.analytics import (
    ByCategoryOut,
    ByStatusOut,
    CategorySpend,
    DimensionBreakdown,
    DimensionSpend,
    SpendOverTimeOut,
    StatusBucket,
    SummaryOut,
    TimeBucket,
    TopVendorsOut,
    VendorSpend,
)

# The untagged-spend label lives in the registry so Explore and this fixed
# report bucket identically (ADR-0026 — one dimension registry).
_UNASSIGNED = UNASSIGNED

_ZERO = Decimal("0")


async def _pick_currency(
    db: AsyncSession, org_id: str, currency: str | None
) -> tuple[str, list[str]]:
    """Resolve the report currency + the currencies present for the tenant.

    Exact shape of `issued_reports._pick_currency` (the AR reports' canonical
    pattern, C1.7): grouped over `org_id` alone (not date-scoped, so "what
    currencies exist" doesn't flicker with the report window), most-used wins
    when none is requested explicitly.
    """
    rows = list(
        await db.execute(
            select(Invoice.currency, func.count(Invoice.id))
            .where(Invoice.org_id == org_id)
            .group_by(Invoice.currency)
            .order_by(func.count(Invoice.id).desc())
        )
    )
    available = sorted(c for c, _ in rows)
    if currency:
        return currency.upper(), available
    return (rows[0][0] if rows else "EUR"), available


def _month_expr():
    """DB-portable 'YYYY-MM' bucket for issue_date."""
    if settings.is_sqlite:
        return func.strftime("%Y-%m", Invoice.issue_date)
    return func.to_char(Invoice.issue_date, "FMYYYY-MM")


def _scope(
    stmt: Select, org_id: str, currency: str, start: date | None, end: date | None
) -> Select:
    stmt = stmt.where(Invoice.org_id == org_id, Invoice.currency == currency)
    if start is not None:
        stmt = stmt.where(Invoice.issue_date >= start)
    if end is not None:
        stmt = stmt.where(Invoice.issue_date <= end)
    return stmt


async def summary(
    db: AsyncSession,
    org_id: str,
    start: date | None,
    end: date | None,
    currency: str | None = None,
) -> SummaryOut:
    cur, available = await _pick_currency(db, org_id, currency)
    base = _scope(
        select(
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total), 0),
            func.coalesce(func.sum(Invoice.tax_amount), 0),
        ),
        org_id,
        cur,
        start,
        end,
    )
    count, total, tax = (await db.execute(base)).one()

    unpaid_stmt = _scope(
        select(func.coalesce(func.sum(Invoice.total), 0)), org_id, cur, start, end
    ).where(Invoice.status.in_([InvoiceStatus.pending, InvoiceStatus.overdue]))
    unpaid = await db.scalar(unpaid_stmt)

    # Vendor count is scoped to the SAME currency as the amounts above, so the
    # dashboard's "N vendors" and "total spend" figures describe the same set
    # of invoices (a count is not a money sum, but mixing its scope with the
    # amounts' scope would still mislead).
    vendor_count = await db.scalar(
        select(func.count(func.distinct(Vendor.id)))
        .select_from(Invoice)
        .join(Vendor, Vendor.id == Invoice.vendor_id)
        .where(Invoice.org_id == org_id, Invoice.currency == cur)
    )

    count = count or 0
    total = Decimal(total or 0)
    avg = (total / count).quantize(Decimal("0.01")) if count else _ZERO

    return SummaryOut(
        total_invoices=count,
        total_spend=total,
        total_tax=Decimal(tax or 0),
        unpaid_amount=Decimal(unpaid or 0),
        avg_invoice=avg,
        vendor_count=vendor_count or 0,
        currency=cur,
        available_currencies=available,
    )


async def spend_over_time(
    db: AsyncSession,
    org_id: str,
    start: date | None,
    end: date | None,
    currency: str | None = None,
) -> SpendOverTimeOut:
    cur, available = await _pick_currency(db, org_id, currency)
    month = _month_expr().label("period")
    stmt = (
        _scope(
            select(
                month,
                func.coalesce(func.sum(Invoice.total), 0),
                func.count(Invoice.id),
            ),
            org_id,
            cur,
            start,
            end,
        )
        .group_by(month)
        .order_by(month)
    )
    rows = (await db.execute(stmt)).all()
    buckets = [TimeBucket(period=r[0], total=Decimal(r[1] or 0), invoice_count=r[2]) for r in rows]
    return SpendOverTimeOut(currency=cur, available_currencies=available, rows=buckets)


async def top_vendors(
    db: AsyncSession,
    org_id: str,
    start: date | None,
    end: date | None,
    limit: int = 10,
    currency: str | None = None,
) -> TopVendorsOut:
    cur, available = await _pick_currency(db, org_id, currency)
    total = func.coalesce(func.sum(Invoice.total), 0).label("total")
    stmt = (
        _scope(
            select(Vendor.id, Vendor.name, total, func.count(Invoice.id)).join(
                Vendor, Vendor.id == Invoice.vendor_id
            ),
            org_id,
            cur,
            start,
            end,
        )
        .group_by(Vendor.id, Vendor.name)
        .order_by(total.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    vendors = [
        VendorSpend(vendor_id=r[0], vendor_name=r[1], total=Decimal(r[2] or 0), invoice_count=r[3])
        for r in rows
    ]
    return TopVendorsOut(currency=cur, available_currencies=available, rows=vendors)


async def by_category(
    db: AsyncSession,
    org_id: str,
    start: date | None,
    end: date | None,
    currency: str | None = None,
) -> ByCategoryOut:
    cur, available = await _pick_currency(db, org_id, currency)
    total = func.coalesce(func.sum(LineItem.amount), 0).label("total")
    stmt = (
        _scope(
            select(LineItem.category, total).join(Invoice, Invoice.id == LineItem.invoice_id),
            org_id,
            cur,
            start,
            end,
        )
        .group_by(LineItem.category)
        .order_by(total.desc())
    )
    rows = (await db.execute(stmt)).all()
    cats = [CategorySpend(category=r[0], total=Decimal(r[1] or 0)) for r in rows]
    return ByCategoryOut(currency=cur, available_currencies=available, rows=cats)


async def by_dimension(
    db: AsyncSession,
    org_id: str,
    dimension: str,
    start: date | None,
    end: date | None,
    currency: str | None = None,
) -> DimensionBreakdown:
    """Group invoice spend by a cost-allocation dimension (cost_center, vehicle, …).

    Untagged invoices roll up under "(unassigned)" so the breakdown always sums
    to the tenant's total spend for the window, IN THE RESOLVED CURRENCY only
    (C1.7) — `total` never blends currencies.
    """
    if not is_dimension(dimension):
        raise ValueError(f"unknown dimension '{dimension}'")
    cur, available = await _pick_currency(db, org_id, currency)
    col = getattr(Invoice, dimension)
    total = func.coalesce(func.sum(Invoice.total), 0).label("total")
    stmt = (
        _scope(select(col, total, func.count(Invoice.id)), org_id, cur, start, end)
        .group_by(col)
        .order_by(total.desc())
    )
    rows = (await db.execute(stmt)).all()

    out = [
        DimensionSpend(value=(r[0] or _UNASSIGNED), total=Decimal(r[1] or 0), invoice_count=r[2])
        for r in rows
    ]
    grand = sum((r.total for r in out), start=_ZERO)
    return DimensionBreakdown(
        dimension=dimension,
        label=DIMENSIONS[dimension],
        rows=out,
        total=grand,
        currency=cur,
        available_currencies=available,
    )


async def by_status(
    db: AsyncSession,
    org_id: str,
    start: date | None,
    end: date | None,
    currency: str | None = None,
) -> ByStatusOut:
    cur, available = await _pick_currency(db, org_id, currency)
    stmt = _scope(
        select(
            Invoice.status,
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total), 0),
        ),
        org_id,
        cur,
        start,
        end,
    ).group_by(Invoice.status)
    rows = (await db.execute(stmt)).all()
    buckets = [
        StatusBucket(
            status=r[0].value if hasattr(r[0], "value") else str(r[0]),
            count=r[1],
            total=Decimal(r[2] or 0),
        )
        for r in rows
    ]
    return ByStatusOut(currency=cur, available_currencies=available, rows=buckets)
