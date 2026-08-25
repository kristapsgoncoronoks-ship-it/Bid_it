"""Supplier cost analytics, phase 1 (WO-G, docs/design/supplier-cost-analytics.md).

Read models ONLY, over data the tenant already owns: per supplier × item
price history from captured invoice lines, latest-vs-trailing change
detection, and the KPI headline. No new tables, no external data (ADR-0027
stands untouched) — Postgres-side aggregation IS the engine (§2b of the
design doc; the same reason analytics/explore/benchmark aggregate in the
database: every analytical query stays inside the three-layer tenant guard).

The discipline this module owns:

- **Single-currency scope** (C1.7, the AR-reports pattern): an explicit
  currency filter wins, else the tenant's most-used; other currencies are
  surfaced in `available_currencies`, never folded in silently.
- **An item is its normalised description** (lower/trimmed). Phase 1 has no
  item master on purpose — the price list that names canonical items is
  phase 2's agreed-price table.
- **Only meaningful lines count**: quantity > 0 and unit_price > 0 — a
  zero-priced or unquantified line is a note, not a price point.
- **Change detection needs history**: an item enters the movers list only
  with >= `MIN_POINTS` price points, and the trailing baseline is the
  QUANTITY-WEIGHTED average (sum(amount)/sum(quantity)) of everything
  before the latest date — a single early outlier cannot masquerade as a
  trend, and the latest observation never dilutes its own baseline.
- Industry-neutral throughout: supplier, item, price — nouns only in docs.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, LineItem
from app.models.vendor import Vendor

_ZERO = Decimal("0")
_CENT = Decimal("0.01")
_PCT = Decimal("0.1")

#: An item qualifies for change detection only with this many price points.
MIN_POINTS = 2
#: Default lookback for the movers/KPI window.
DEFAULT_WINDOW_DAYS = 365


def _money(v: Decimal) -> str:
    return str(v.quantize(_CENT, rounding=ROUND_HALF_UP))


def _pct(v: Decimal) -> str:
    return str(v.quantize(_PCT, rounding=ROUND_HALF_UP))


async def _pick_currency(
    db: AsyncSession, org_id: str, currency: str | None
) -> tuple[str, list[str]]:
    """The AR-reports pattern (C1.7) — each reporting module owns its copy."""
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


def _item_key() -> ColumnElement[str]:
    return func.lower(func.trim(LineItem.description))


def _lines_scope(stmt: Select, org_id: str, currency: str, start: date | None) -> Select:
    stmt = (
        stmt.join(Invoice, LineItem.invoice_id == Invoice.id)
        .join(Vendor, Invoice.vendor_id == Vendor.id)
        .where(
            Invoice.org_id == org_id,
            Invoice.currency == currency,
            LineItem.quantity > 0,
            LineItem.unit_price > 0,
        )
    )
    if start is not None:
        stmt = stmt.where(Invoice.issue_date >= start)
    return stmt


async def _daily_points(
    db: AsyncSession,
    org_id: str,
    currency: str,
    start: date | None,
    *,
    vendor_id: str | None = None,
    item: str | None = None,
) -> list:
    """(vendor_id, vendor_name, item, category, issue_date, qty, spend) —
    one row per supplier × item × day, aggregated database-side. The small
    grouped result is what Python then folds into series and movers."""
    stmt = _lines_scope(
        select(
            Invoice.vendor_id,
            Vendor.name,
            _item_key().label("item"),
            func.min(LineItem.category).label("category"),
            Invoice.issue_date,
            func.sum(LineItem.quantity).label("qty"),
            func.sum(LineItem.amount).label("spend"),
        ),
        org_id,
        currency,
        start,
    )
    if vendor_id is not None:
        stmt = stmt.where(Invoice.vendor_id == vendor_id)
    if item is not None:
        stmt = stmt.where(_item_key() == item.strip().lower())
    stmt = stmt.group_by(Invoice.vendor_id, Vendor.name, _item_key(), Invoice.issue_date)
    stmt = stmt.order_by(Invoice.vendor_id, _item_key(), Invoice.issue_date)
    return list(await db.execute(stmt))


def _fold_changes(rows: list, min_points: int) -> list[dict]:
    """Group daily points per (supplier, item); compute latest unit price vs
    the weighted trailing average of everything before the latest date."""
    out: list[dict] = []
    group: list = []

    def flush() -> None:
        if len(group) < min_points:
            return
        *earlier, last = group
        base_qty = sum((r.qty for r in earlier), _ZERO)
        base_spend = sum((r.spend for r in earlier), _ZERO)
        if base_qty <= 0:
            return
        trailing = base_spend / base_qty
        latest = last.spend / last.qty if last.qty else _ZERO
        if trailing <= 0:
            return
        pct = (latest - trailing) / trailing * 100
        out.append(
            {
                "vendor_id": group[0].vendor_id,
                "vendor_name": group[0].name,
                "item": group[0].item,
                "category": group[0].category,
                "points": len(group),
                "latest_price": _money(latest),
                "latest_date": last.issue_date.isoformat(),
                "trailing_avg": _money(trailing),
                "pct_change": _pct(pct),
            }
        )

    for r in rows:
        if group and (r.vendor_id != group[0].vendor_id or r.item != group[0].item):
            flush()
            group = []
        group.append(r)
    if group:
        flush()
    return out


async def cost_changes(
    db: AsyncSession,
    org_id: str,
    *,
    currency: str | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_points: int = MIN_POINTS,
    limit: int = 50,
) -> dict:
    """The movers list: every supplier × item with enough history, sorted by
    absolute % change, capped at `limit` (the cap is stated in the payload —
    no silent truncation)."""
    cur, available = await _pick_currency(db, org_id, currency)
    start = date.today() - timedelta(days=window_days)
    rows = _fold_changes(await _daily_points(db, org_id, cur, start), min_points)
    rows.sort(key=lambda r: abs(Decimal(r["pct_change"])), reverse=True)
    return {
        "currency": cur,
        "available_currencies": available,
        "window_days": window_days,
        "total_tracked": len(rows),
        "rows": rows[:limit],
    }


async def cost_kpis(db: AsyncSession, org_id: str, *, currency: str | None = None) -> dict:
    """The headline cards: how many suppliers/items are tracked, how many
    moved up/down, and the single biggest move — all from the same fold as
    the movers list, so the cards can never disagree with the table."""
    changes = await cost_changes(db, org_id, currency=currency, limit=10_000)
    rows = changes["rows"]
    risers = [r for r in rows if Decimal(r["pct_change"]) > 0]
    fallers = [r for r in rows if Decimal(r["pct_change"]) < 0]
    biggest = rows[0] if rows else None
    return {
        "currency": changes["currency"],
        "available_currencies": changes["available_currencies"],
        "window_days": changes["window_days"],
        "suppliers": len({r["vendor_id"] for r in rows}),
        "tracked_items": len(rows),
        "risers": len(risers),
        "fallers": len(fallers),
        "biggest_mover": biggest,
    }


async def price_history(
    db: AsyncSession,
    org_id: str,
    *,
    vendor_id: str,
    item: str,
    currency: str | None = None,
    months: int = 12,
) -> dict:
    """The graph feed: monthly quantity-weighted average unit price for ONE
    supplier × item, oldest first. Months with no purchases are simply
    absent — the chart shows real observations, not interpolation."""
    cur, available = await _pick_currency(db, org_id, currency)
    start = (date.today().replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    rows = await _daily_points(db, org_id, cur, start, vendor_id=vendor_id, item=item)
    by_month: dict[str, dict[str, Decimal]] = {}
    for r in rows:
        m = r.issue_date.strftime("%Y-%m")
        agg = by_month.setdefault(m, {"qty": _ZERO, "spend": _ZERO, "points": _ZERO})
        agg["qty"] += r.qty
        agg["spend"] += r.spend
        agg["points"] += 1
    series = [
        {
            "month": m,
            "avg_price": _money(v["spend"] / v["qty"]),
            "quantity": str(v["qty"]),
            "spend": _money(v["spend"]),
            "points": int(v["points"]),
        }
        for m, v in sorted(by_month.items())
        if v["qty"] > 0
    ]
    return {
        "currency": cur,
        "available_currencies": available,
        "vendor_id": vendor_id,
        "item": item.strip().lower(),
        "series": series,
    }
