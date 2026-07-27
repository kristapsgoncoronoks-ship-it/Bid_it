"""Supplier benchmarking — independently and combined.

Two lenses over one tenant's validated invoices:

  • Independent  — a scorecard per supplier (spend, invoices, effective tax rate,
    paid ratio, activity window, spend share).
  • Combined     — cross-supplier price comparison per category. Each supplier's
    EFFECTIVE unit price (spend / quantity within a category) is compared to the
    combined average and to the cheapest supplier, surfacing the €-savings
    opportunity of consolidating that category to the best rate.

Basis: effective unit price = line spend / line quantity within a category.
Comparisons are indicative — quantities are only loosely comparable across
different products in the same category — and are advisory, never a gate.
All aggregation happens in the database; per-category math is finished in Python.

C1.7/WO-24: both lenses sum money (per-vendor / per-category spend), so both
are scoped to a SINGLE currency — the AR-reports pattern (`analytics.
_pick_currency`): an explicit `currency` filter wins, else the tenant's
most-used currency; every other currency present is surfaced, never folded
into the same total (§4.14). A vendor billing in both EUR and USD used to get
one blended `total_spend` — that is exactly the defect this closes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import q as _q
from app.models.invoice import Invoice, InvoiceStatus, LineItem
from app.models.vendor import Vendor
from app.schemas.benchmark import (
    BenchmarkSummary,
    CategoryBenchmark,
    CombinedBenchmark,
    SupplierBenchmark,
    SupplierBenchmarkListOut,
    SupplierPricePoint,
)

_ZERO = Decimal("0")
_PCT = Decimal("0.1")


async def _pick_currency(
    db: AsyncSession, org_id: str, currency: str | None
) -> tuple[str, list[str]]:
    """Same shape as `analytics._pick_currency` / `issued_reports._pick_currency`
    (the AR-reports pattern, C1.7) — kept local rather than shared to match the
    existing convention of each reporting module owning its own copy."""
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


def _scope(
    stmt: Select, org_id: str, currency: str, start: date | None, end: date | None
) -> Select:
    stmt = stmt.where(Invoice.org_id == org_id, Invoice.currency == currency)
    if start is not None:
        stmt = stmt.where(Invoice.issue_date >= start)
    if end is not None:
        stmt = stmt.where(Invoice.issue_date <= end)
    return stmt


# --------------------------------------------------------------------------- #
# Independent per-supplier scorecards
# --------------------------------------------------------------------------- #
async def supplier_benchmarks(
    db: AsyncSession,
    org_id: str,
    start: date | None,
    end: date | None,
    currency: str | None = None,
) -> SupplierBenchmarkListOut:
    cur, available = await _pick_currency(db, org_id, currency)
    paid_sum = func.coalesce(
        func.sum(case((Invoice.status == InvoiceStatus.paid, Invoice.total), else_=0)), 0
    )
    stmt = _scope(
        select(
            Vendor.id,
            Vendor.name,
            Vendor.country,
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total), 0),
            func.coalesce(func.sum(Invoice.subtotal), 0),
            func.coalesce(func.sum(Invoice.tax_amount), 0),
            paid_sum,
            func.min(Invoice.issue_date),
            func.max(Invoice.issue_date),
        ).join(Vendor, Vendor.id == Invoice.vendor_id),
        org_id,
        cur,
        start,
        end,
    ).group_by(Vendor.id, Vendor.name, Vendor.country)
    rows = (await db.execute(stmt)).all()

    # Category counts per vendor (separate join through line_items), same
    # single-currency scope as the spend query above.
    cat_stmt = _scope(
        select(Invoice.vendor_id, func.count(func.distinct(LineItem.category))).join(
            LineItem, LineItem.invoice_id == Invoice.id
        ),
        org_id,
        cur,
        start,
        end,
    ).group_by(Invoice.vendor_id)
    cat_counts: dict[str, int] = dict((await db.execute(cat_stmt)).tuples().all())

    grand_total = sum((Decimal(r[4] or 0) for r in rows), start=_ZERO)

    out: list[SupplierBenchmark] = []
    for r in rows:
        (vid, name, country, count, total, subtotal, tax, paid, first, last) = r
        total = Decimal(total or 0)
        subtotal = Decimal(subtotal or 0)
        tax = Decimal(tax or 0)
        count = count or 0
        eff_tax = _q(tax / subtotal * 100, _PCT) if subtotal > 0 else _ZERO
        paid_ratio = _q(Decimal(paid or 0) / total, Decimal("0.001")) if total > 0 else _ZERO
        avg = _q(total / count) if count else _ZERO
        share = _q(total / grand_total * 100, _PCT) if grand_total > 0 else _ZERO
        out.append(
            SupplierBenchmark(
                vendor_id=vid,
                vendor_name=name,
                country=country,
                invoice_count=count,
                total_spend=_q(total),
                total_tax=_q(tax),
                avg_invoice=avg,
                effective_tax_rate=eff_tax,
                paid_ratio=paid_ratio,
                category_count=cat_counts.get(vid, 0),
                first_invoice=first,
                last_invoice=last,
                spend_share=share,
            )
        )
    out.sort(key=lambda s: s.total_spend, reverse=True)
    return SupplierBenchmarkListOut(currency=cur, available_currencies=available, rows=out)


# --------------------------------------------------------------------------- #
# Combined cross-supplier price benchmark
# --------------------------------------------------------------------------- #
async def combined_benchmark(
    db: AsyncSession,
    org_id: str,
    start: date | None,
    end: date | None,
    currency: str | None = None,
) -> CombinedBenchmark:
    cur, available = await _pick_currency(db, org_id, currency)
    # Per (category, vendor): total spend and total quantity — scoped to `cur`
    # alone (C1.7): unit prices/spend from different currencies are not
    # comparable without conversion, so mixing them into one "cheapest
    # vendor" verdict would be actively misleading, not just mislabeled.
    stmt = _scope(
        select(
            LineItem.category,
            Vendor.id,
            Vendor.name,
            func.coalesce(func.sum(LineItem.amount), 0),
            func.coalesce(func.sum(LineItem.quantity), 0),
        )
        .join(Invoice, Invoice.id == LineItem.invoice_id)
        .join(Vendor, Vendor.id == Invoice.vendor_id),
        org_id,
        cur,
        start,
        end,
    ).group_by(LineItem.category, Vendor.id, Vendor.name)
    rows = (await db.execute(stmt)).all()

    # Bucket by category.
    by_cat: dict[str, list[tuple]] = {}
    for category, vid, vname, spend, qty in rows:
        by_cat.setdefault(category, []).append((vid, vname, Decimal(spend or 0), Decimal(qty or 0)))

    categories: list[CategoryBenchmark] = []
    total_spend_all = _ZERO
    total_savings = _ZERO
    multi_supplier = 0

    for category, entries in by_cat.items():
        # Effective unit price per supplier (skip suppliers with zero quantity).
        priced = [
            (vid, vname, spend, qty, spend / qty) for (vid, vname, spend, qty) in entries if qty > 0
        ]
        cat_spend = sum((e[2] for e in entries), start=_ZERO)
        cat_qty = sum((e[3] for e in entries), start=_ZERO)
        total_spend_all += cat_spend
        if not priced:
            continue

        combined_avg = sum((e[2] for e in priced), start=_ZERO) / sum(
            (e[3] for e in priced), start=_ZERO
        )
        cheapest = min(priced, key=lambda e: e[4])
        cheapest_unit = cheapest[4]

        suppliers: list[SupplierPricePoint] = []
        cat_savings = _ZERO
        for vid, vname, spend, qty, unit in sorted(priced, key=lambda e: e[4]):
            overspend = (unit - cheapest_unit) * qty
            overspend = _q(overspend) if overspend > 0 else _q(_ZERO)
            cat_savings += overspend
            deviation = (
                _q((unit - combined_avg) / combined_avg * 100, _PCT) if combined_avg > 0 else _ZERO
            )
            suppliers.append(
                SupplierPricePoint(
                    vendor_id=vid,
                    vendor_name=vname,
                    unit_price=_q(unit, Decimal("0.0001")),
                    quantity=_q(qty, Decimal("0.001")),
                    spend=_q(spend),
                    deviation_pct=deviation,
                    overspend_vs_cheapest=overspend,
                    is_cheapest=(vid == cheapest[0]),
                )
            )

        if len(priced) > 1:
            multi_supplier += 1
        total_savings += cat_savings
        categories.append(
            CategoryBenchmark(
                category=category,
                supplier_count=len(priced),
                total_spend=_q(cat_spend),
                total_quantity=_q(cat_qty, Decimal("0.001")),
                combined_avg_unit=_q(combined_avg, Decimal("0.0001")),
                cheapest_vendor_id=cheapest[0],
                cheapest_vendor_name=cheapest[1],
                cheapest_unit=_q(cheapest_unit, Decimal("0.0001")),
                savings_opportunity=_q(cat_savings),
                suppliers=suppliers,
            )
        )

    # Biggest savings first.
    categories.sort(key=lambda c: c.savings_opportunity, reverse=True)

    summary = BenchmarkSummary(
        supplier_count=len({vid for _, entries in by_cat.items() for (vid, *_rest) in entries}),
        total_spend=_q(total_spend_all),
        categories_analyzed=len(categories),
        multi_supplier_categories=multi_supplier,
        total_savings_opportunity=_q(total_savings),
        currency=cur,
        available_currencies=available,
    )
    return CombinedBenchmark(summary=summary, categories=categories)
