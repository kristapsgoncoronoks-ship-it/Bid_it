"""Monthly budgeting over received invoices (household / personal budgeting).

Actuals are drawn from the SAME received-invoice fact grain as the analytics
(line_items ⋈ invoices), org-scoped. Household budgeting cares about money out of
pocket, so an actual is the **gross** line amount (net + VAT), converted to EUR at
the invoice's ECB rate (`total_eur/total` ratio) so multi-currency bills compare
cleanly. A `BudgetTarget` sets a recurring monthly limit per category; the
summary compares target vs actual for a chosen month.

C1.7/WO-24: this module deliberately keeps a SINGLE canonical EUR total (unlike
analytics/benchmark's "pick one currency, filter, surface the rest" — household
budgeting wants one number across every category, so conversion is the right
shape here, not filtering). The bug this order fixes is narrower: an invoice
whose `total_eur` is `NULL` (currency != EUR and `fx_source == "unknown"` — no
rate was ever available) used to fall back to a ratio of 1, i.e. it was
silently counted as if it were already EUR. `_convertible()` now EXCLUDES such
an invoice from every actual/trend total instead of guessing (§4.15: unknown
never guesses), and `overview()` reports how many invoices were excluded
(`excluded_unconverted`) so the gap is visible, not silent.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.money import q2
from app.models.budget import BudgetTarget
from app.models.invoice import Invoice, LineItem


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def parse_month(value: str | None) -> tuple[int, int]:
    """Parse 'YYYY-MM' → (year, month). Raises ValueError on bad input."""
    if not value:
        raise ValueError("month is required as YYYY-MM")
    parts = value.split("-")
    if len(parts) != 2:
        raise ValueError("month must be YYYY-MM")
    year, month = int(parts[0]), int(parts[1])
    if not (1 <= month <= 12) or year < 1970:
        raise ValueError("month must be YYYY-MM")
    return year, month


def _month_key():
    if settings.is_sqlite:
        return func.strftime("%Y-%m", Invoice.issue_date)
    return func.to_char(Invoice.issue_date, "FMYYYY-MM")


def _convertible():
    """True for an invoice this module can honestly express in EUR: EUR-native
    (ratio 1 by convention — not a guess), or a foreign invoice with a recorded
    `total_eur` (a stated rate or a resolved ECB rate). False for a foreign
    invoice with NO recorded conversion (`fx_source == "unknown"`) — that
    invoice is EXCLUDED from every actual/trend total below, never counted at
    a guessed 1:1 parity (§4.15; mirrors `payment_run.eur_of`'s fail-closed
    rule, adapted to an aggregate: exclude-and-report rather than raise, since
    this is a read-only report, not a payment action)."""
    return (Invoice.currency == "EUR") | (Invoice.total_eur.is_not(None))


# Gross line amount (net + VAT) converted to EUR by the invoice's ECB ratio.
# Only reached for rows `_convertible()` already let through, so the ratio is
# always either 1 (EUR-native) or a real recorded rate — never a guess.
def _gross_eur():
    ratio = func.coalesce(Invoice.total_eur, Invoice.total) / func.nullif(Invoice.total, 0)
    gross = LineItem.amount + LineItem.amount * LineItem.tax_rate / 100
    return func.coalesce(func.sum(gross * ratio), 0)


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
async def list_targets(db: AsyncSession, org_id: str) -> list[BudgetTarget]:
    rows = await db.scalars(
        select(BudgetTarget).where(BudgetTarget.org_id == org_id).order_by(BudgetTarget.category)
    )
    return list(rows)


async def set_target(
    db: AsyncSession, org_id: str, category: str, monthly_limit: Decimal
) -> BudgetTarget:
    category = category.strip()[:80].lower() or "uncategorized"
    row = await db.scalar(
        select(BudgetTarget).where(BudgetTarget.org_id == org_id, BudgetTarget.category == category)
    )
    if row is None:
        row = BudgetTarget(org_id=org_id, category=category, monthly_limit=q2(monthly_limit))
        db.add(row)
    else:
        row.monthly_limit = q2(monthly_limit)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_target(db: AsyncSession, org_id: str, category: str) -> bool:
    row = await db.scalar(
        select(BudgetTarget).where(
            BudgetTarget.org_id == org_id, BudgetTarget.category == category.lower()
        )
    )
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


# --------------------------------------------------------------------------- #
# Actuals + summary
# --------------------------------------------------------------------------- #
async def actuals_for_month(
    db: AsyncSession, org_id: str, start: date, end: date
) -> tuple[dict[str, Decimal], int]:
    """Per-category actuals for `[start, end]`, plus the count of invoices in
    that window that were EXCLUDED because they cannot be honestly converted
    (`_convertible()` false) — never silently guessed at parity (§4.15)."""
    stmt = (
        select(LineItem.category, _gross_eur())
        .select_from(LineItem)
        .join(Invoice, Invoice.id == LineItem.invoice_id)
        .where(
            Invoice.org_id == org_id,
            Invoice.issue_date >= start,
            Invoice.issue_date <= end,
            _convertible(),
        )
        .group_by(LineItem.category)
    )
    rows = (await db.execute(stmt)).all()
    excluded = await db.scalar(
        select(func.count(func.distinct(Invoice.id))).where(
            Invoice.org_id == org_id,
            Invoice.issue_date >= start,
            Invoice.issue_date <= end,
            ~_convertible(),
        )
    )
    return {(c or "uncategorized"): q2(Decimal(str(v or 0))) for c, v in rows}, (excluded or 0)


async def trend(
    db: AsyncSession, org_id: str, months: list[str], budget_total: Decimal
) -> list[dict]:
    """Actual EUR spend per month for the given YYYY-MM list, paired with the
    (flat) monthly budget total for a plan-vs-actual line.

    Same exclusion rule as `actuals_for_month` (`_convertible()`) — a
    non-convertible invoice is left out of every month's total rather than
    guessed at parity. This function does not report a per-month excluded
    count (only `overview()`'s anchor month does, via `actuals_for_month`);
    the totals themselves are still correct across the whole series."""
    if not months:
        return []
    key = _month_key()
    first_year, first_month = parse_month(min(months))
    start, _ = month_bounds(first_year, first_month)
    stmt = (
        select(key.label("m"), _gross_eur())
        .select_from(LineItem)
        .join(Invoice, Invoice.id == LineItem.invoice_id)
        .where(Invoice.org_id == org_id, Invoice.issue_date >= start, _convertible())
        .group_by(key)
    )
    got = {m: Decimal(str(v or 0)) for m, v in (await db.execute(stmt)).all()}
    return [
        {"month": m, "actual": str(q2(got.get(m, Decimal("0")))), "budget": str(q2(budget_total))}
        for m in months
    ]


def _recent_months(year: int, month: int, count: int) -> list[str]:
    out: list[str] = []
    y, m = year, month
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


@dataclass
class BudgetRow:
    category: str
    budget: Decimal
    actual: Decimal


async def overview(db: AsyncSession, org_id: str, year: int, month: int) -> dict:
    start, end = month_bounds(year, month)
    targets = {t.category: t.monthly_limit for t in await list_targets(db, org_id)}
    actuals, excluded_unconverted = await actuals_for_month(db, org_id, start, end)

    categories = sorted(set(targets) | set(actuals))
    rows = []
    budget_total = Decimal("0")
    actual_total = Decimal("0")
    for cat in categories:
        budget = q2(targets.get(cat, Decimal("0")))
        actual = q2(actuals.get(cat, Decimal("0")))
        budget_total += budget
        actual_total += actual
        remaining = q2(budget - actual)
        pct = (
            int((actual / budget * 100).to_integral_value(rounding=ROUND_HALF_UP))
            if budget > 0
            else None
        )
        rows.append(
            {
                "category": cat,
                "budget": str(budget),
                "actual": str(actual),
                "remaining": str(remaining),
                "pct": pct,
                "over": budget > 0 and actual > budget,
                "untargeted": cat not in targets,
            }
        )

    budget_total = q2(budget_total)
    actual_total = q2(actual_total)
    trend_rows = await trend(db, org_id, _recent_months(year, month, 6), budget_total)

    return {
        "month": f"{year:04d}-{month:02d}",
        "currency": "EUR",
        "total_budget": str(budget_total),
        "total_actual": str(actual_total),
        "total_remaining": str(q2(budget_total - actual_total)),
        "over_budget": budget_total > 0 and actual_total > budget_total,
        "rows": rows,
        "trend": trend_rows,
        # C1.7/WO-24: invoices in `[start, end]` that could not be honestly
        # converted to EUR (no rate ever resolved) — excluded from every total
        # above, never guessed at parity. Non-zero means the totals are an
        # honest UNDER-count, not a wrong number.
        "excluded_unconverted": excluded_unconverted,
    }
