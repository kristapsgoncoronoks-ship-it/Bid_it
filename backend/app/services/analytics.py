"""Read-only spend analytics over a single tenant's invoices.

Every query is grouped/aggregated in the database (not in Python) so it stays
fast as invoice volume grows. All functions take an explicit `org_id` and an
optional `[start, end]` issue-date window.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dimensions import DIMENSIONS, is_dimension
from app.models.invoice import Invoice, InvoiceStatus, LineItem
from app.models.vendor import Vendor
from app.schemas.analytics import (
    CategorySpend,
    DimensionBreakdown,
    DimensionSpend,
    StatusBucket,
    SummaryOut,
    TimeBucket,
    VendorSpend,
)

_UNASSIGNED = "(unassigned)"

_ZERO = Decimal("0")


def _month_expr():
    """DB-portable 'YYYY-MM' bucket for issue_date."""
    if settings.is_sqlite:
        return func.strftime("%Y-%m", Invoice.issue_date)
    return func.to_char(Invoice.issue_date, "FMYYYY-MM")


def _scope(stmt: Select, org_id: str, start: date | None, end: date | None) -> Select:
    stmt = stmt.where(Invoice.org_id == org_id)
    if start is not None:
        stmt = stmt.where(Invoice.issue_date >= start)
    if end is not None:
        stmt = stmt.where(Invoice.issue_date <= end)
    return stmt


async def summary(
    db: AsyncSession, org_id: str, start: date | None, end: date | None
) -> SummaryOut:
    base = _scope(
        select(
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total), 0),
            func.coalesce(func.sum(Invoice.tax_amount), 0),
        ),
        org_id,
        start,
        end,
    )
    count, total, tax = (await db.execute(base)).one()

    unpaid_stmt = _scope(
        select(func.coalesce(func.sum(Invoice.total), 0)), org_id, start, end
    ).where(Invoice.status.in_([InvoiceStatus.pending, InvoiceStatus.overdue]))
    unpaid = await db.scalar(unpaid_stmt)

    vendor_count = await db.scalar(
        select(func.count(func.distinct(Vendor.id)))
        .select_from(Invoice)
        .join(Vendor, Vendor.id == Invoice.vendor_id)
        .where(Invoice.org_id == org_id)
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
        currency="EUR",
    )


async def spend_over_time(
    db: AsyncSession, org_id: str, start: date | None, end: date | None
) -> list[TimeBucket]:
    month = _month_expr().label("period")
    stmt = _scope(
        select(
            month,
            func.coalesce(func.sum(Invoice.total), 0),
            func.count(Invoice.id),
        ),
        org_id,
        start,
        end,
    ).group_by(month).order_by(month)
    rows = (await db.execute(stmt)).all()
    return [
        TimeBucket(period=r[0], total=Decimal(r[1] or 0), invoice_count=r[2])
        for r in rows
    ]


async def top_vendors(
    db: AsyncSession, org_id: str, start: date | None, end: date | None, limit: int = 10
) -> list[VendorSpend]:
    total = func.coalesce(func.sum(Invoice.total), 0).label("total")
    stmt = _scope(
        select(Vendor.id, Vendor.name, total, func.count(Invoice.id))
        .join(Vendor, Vendor.id == Invoice.vendor_id),
        org_id,
        start,
        end,
    ).group_by(Vendor.id, Vendor.name).order_by(total.desc()).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [
        VendorSpend(vendor_id=r[0], vendor_name=r[1], total=Decimal(r[2] or 0), invoice_count=r[3])
        for r in rows
    ]


async def by_category(
    db: AsyncSession, org_id: str, start: date | None, end: date | None
) -> list[CategorySpend]:
    total = func.coalesce(func.sum(LineItem.amount), 0).label("total")
    stmt = _scope(
        select(LineItem.category, total).join(Invoice, Invoice.id == LineItem.invoice_id),
        org_id,
        start,
        end,
    ).group_by(LineItem.category).order_by(total.desc())
    rows = (await db.execute(stmt)).all()
    return [CategorySpend(category=r[0], total=Decimal(r[1] or 0)) for r in rows]


async def by_dimension(
    db: AsyncSession, org_id: str, dimension: str, start: date | None, end: date | None
) -> DimensionBreakdown:
    """Group invoice spend by a cost-allocation dimension (cost_center, vehicle, …).

    Untagged invoices roll up under "(unassigned)" so the breakdown always sums
    to the tenant's total spend for the window.
    """
    if not is_dimension(dimension):
        raise ValueError(f"unknown dimension '{dimension}'")
    col = getattr(Invoice, dimension)
    total = func.coalesce(func.sum(Invoice.total), 0).label("total")
    stmt = _scope(
        select(col, total, func.count(Invoice.id)), org_id, start, end
    ).group_by(col).order_by(total.desc())
    rows = (await db.execute(stmt)).all()

    out = [
        DimensionSpend(value=(r[0] or _UNASSIGNED), total=Decimal(r[1] or 0), invoice_count=r[2])
        for r in rows
    ]
    grand = sum((r.total for r in out), start=_ZERO)
    return DimensionBreakdown(dimension=dimension, label=DIMENSIONS[dimension], rows=out, total=grand)


async def by_status(
    db: AsyncSession, org_id: str, start: date | None, end: date | None
) -> list[StatusBucket]:
    stmt = _scope(
        select(
            Invoice.status,
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total), 0),
        ),
        org_id,
        start,
        end,
    ).group_by(Invoice.status)
    rows = (await db.execute(stmt)).all()
    return [
        StatusBucket(
            status=r[0].value if hasattr(r[0], "value") else str(r[0]),
            count=r[1],
            total=Decimal(r[2] or 0),
        )
        for r in rows
    ]
