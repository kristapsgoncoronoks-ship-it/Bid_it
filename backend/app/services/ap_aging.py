"""Accounts-payable aging worklist + due-date alerting (Phase 16b).

The AP counterpart of the AR dunning ladder: a per-invoice worklist of open
payables (approved → scheduled → partially-paid supplier invoices), each tagged
with its settlement `status`, days overdue, and an aging/`due_soon` bucket, plus a
roll-up used by the daily due-date digest. Read-only over the AP status derivation
(services/ap_status); writes nothing. Tenant-scoped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import money
from app.models.invoice import Invoice, WorkflowState
from app.services import ap_status

DUE_SOON_DAYS = 7
_ZERO = Decimal("0")

# Supplier-invoice states that represent an open payable (same set the cash-position
# dashboard uses). Draft/submitted/rejected/cancelled aren't payable; paid/archived
# are done.
_PAYABLE_STATES = (
    WorkflowState.approved,
    WorkflowState.scheduled_for_payment,
    WorkflowState.partially_paid,
)


@dataclass
class WorklistItem:
    id: str
    invoice_number: str
    vendor_name: str | None
    due_date: date | None
    currency: str
    total: Decimal
    outstanding: Decimal
    status: str
    days_overdue: int
    bucket: str


def _bucket(inv: Invoice, today: date) -> str:
    if ap_status.status_of(inv, today) == ap_status.OVERDUE:
        d = ap_status.days_overdue_of(inv, today)
        if d <= 30:
            return "1-30"
        if d <= 60:
            return "31-60"
        if d <= 90:
            return "61-90"
        return "90+"
    if inv.due_date is not None and 0 <= (inv.due_date - today).days <= DUE_SOON_DAYS:
        return "due_soon"
    return "later"


async def worklist(db: AsyncSession, org_id: str, today: date | None = None) -> list[WorklistItem]:
    """Open payables, soonest-due first, each with its status + aging bucket."""
    today = today or date.today()
    rows = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.org_id == org_id, Invoice.workflow_state.in_(_PAYABLE_STATES))
            .options(selectinload(Invoice.vendor))
            .order_by(Invoice.due_date.asc().nulls_last())
        )
    )
    return [
        WorklistItem(
            id=inv.id,
            invoice_number=inv.invoice_number,
            vendor_name=inv.vendor.name if inv.vendor else None,
            due_date=inv.due_date,
            currency=inv.currency,
            total=money.q2(Decimal(inv.total)),
            outstanding=ap_status.outstanding_of(inv),
            status=ap_status.status_of(inv, today),
            days_overdue=ap_status.days_overdue_of(inv, today),
            bucket=_bucket(inv, today),
        )
        for inv in rows
    ]


@dataclass
class DueSummary:
    """Due-soon/overdue roll-up. Counts span every currency (a count is not a
    money sum); the AMOUNTS are single-currency (`currency`) only — currencies
    that could not be folded in are surfaced in `other_currencies`, never
    silently summed (WO-8 / §4.14: no cross-currency sums without a recorded
    conversion; this follows the AR reports' single-currency pattern)."""

    currency: str = "EUR"
    due_soon_count: int = 0
    due_soon_amount: Decimal = _ZERO
    overdue_count: int = 0
    overdue_amount: Decimal = _ZERO
    other_currencies: tuple[str, ...] = ()


def summarize(items: list[WorklistItem]) -> DueSummary:
    """Roll up the worklist per the DueSummary contract: pick the most-used
    currency among the items needing attention (EUR wins ties), total ONLY that
    currency's outstanding, and surface the rest — a raw sum of PLN + SEK + EUR
    figures labelled EUR was exactly the defect WO-8 removes."""
    attention = [it for it in items if it.status == ap_status.OVERDUE or it.bucket == "due_soon"]
    counts: dict[str, int] = {}
    for it in attention:
        counts[it.currency] = counts.get(it.currency, 0) + 1
    # Most-used currency; EUR on a tie (deterministic, matches the AR reports).
    currency = "EUR"
    if counts:
        best = max(counts.values())
        leaders = sorted(c for c, n in counts.items() if n == best)
        currency = "EUR" if "EUR" in leaders else leaders[0]
    s = DueSummary(currency=currency)
    for it in attention:
        if it.status == ap_status.OVERDUE:
            s.overdue_count += 1
            if it.currency == currency:
                s.overdue_amount += it.outstanding
        else:
            s.due_soon_count += 1
            if it.currency == currency:
                s.due_soon_amount += it.outstanding
    s.due_soon_amount = money.q2(s.due_soon_amount)
    s.overdue_amount = money.q2(s.overdue_amount)
    s.other_currencies = tuple(sorted(set(counts) - {currency}))
    return s


async def summary(db: AsyncSession, org_id: str, today: date | None = None) -> DueSummary:
    return summarize(await worklist(db, org_id, today))
