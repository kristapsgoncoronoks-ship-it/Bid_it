"""Cash-position dashboard (Phase 15).

A single read-only roll-up of the whole cash cycle: accounts RECEIVABLE (issued
invoices — outstanding / overdue / aging, reusing the canonical receivables
report), accounts PAYABLE (received supplier invoices — outstanding / overdue /
scheduled / queued in a run, via the AP status logic), and BANK RECONCILIATION
(how many imported lines are still unmatched). NET position = receivables −
payables. Aggregates existing data; writes nothing. Tenant-scoped.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.bank_import import BankLine
from app.models.invoice import Invoice, WorkflowState
from app.services import issued_reports

_ZERO = Decimal("0")
# Supplier-invoice workflow states that represent an open payable (approved to pay,
# not yet fully settled). Draft/submitted/rejected/cancelled are not yet payable;
# paid/archived are done.
_PAYABLE_STATES = (
    WorkflowState.approved,
    WorkflowState.scheduled_for_payment,
    WorkflowState.partially_paid,
)


async def _ar_summary(db: AsyncSession, org_id: str, today: date) -> dict:
    rep = await issued_reports.receivables_scalars(db, org_id, today=today)
    return {
        "currency": rep.currency,
        "outstanding": money.q2(rep.total_outstanding),
        "overdue": money.q2(rep.overdue_outstanding),
        "avg_days_to_pay": rep.avg_days_to_pay,
        "aging": [
            {"label": b.label, "count": b.count, "outstanding": money.q2(b.outstanding)}
            for b in rep.aging
        ],
    }


async def _ap_summary(db: AsyncSession, org_id: str, today: date, currency: str) -> dict:
    """Open payables, aggregated in the database (PERF-002).

    This used to hydrate every open payable and reduce it in Python for five
    scalars; the perf harness measured it flat only because it seeded no payable
    state (PERF-004) — with real rows it grew 5.5× across 4× of data. The same
    rule per row (`ap_status.outstanding_of` / `status_of == OVERDUE`) is now
    one grouped query.

    BEHAVIOUR CHANGE, deliberate and stated: the amounts are now summed in the
    REPORT CURRENCY only. The Python version summed every payable's outstanding
    regardless of its currency and the roll-up labelled the figure with the AR
    report's currency — PLN + SEK + EUR presented as one EUR number, the exact
    defect WO-8 removed from every other summary (§4.14: no cross-currency sums
    without a recorded conversion). The counts still span every currency (a
    count is not a money sum), and the currencies left out are surfaced in
    `other_currencies`, never silently dropped."""
    from app.services.ap_aging import _outstanding_expr, _overdue_cond

    outstanding = _outstanding_expr()
    in_currency = Invoice.currency == currency
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(case((in_currency, outstanding), else_=0)), 0),
                func.coalesce(
                    func.sum(case((and_(in_currency, _overdue_cond(today)), outstanding), else_=0)),
                    0,
                ),
                func.count(Invoice.id),
                func.sum(
                    case(
                        (Invoice.workflow_state == WorkflowState.scheduled_for_payment, 1), else_=0
                    )
                ),
                func.sum(case((Invoice.payment_run_id.is_not(None), 1), else_=0)),
            ).where(Invoice.org_id == org_id, Invoice.workflow_state.in_(_PAYABLE_STATES))
        )
    ).one()
    others = await db.scalars(
        select(Invoice.currency)
        .where(
            Invoice.org_id == org_id,
            Invoice.workflow_state.in_(_PAYABLE_STATES),
            Invoice.currency != currency,
        )
        .distinct()
    )
    return {
        "outstanding": money.q2(Decimal(row[0] or 0)),
        "overdue": money.q2(Decimal(row[1] or 0)),
        "count": int(row[2] or 0),
        "scheduled": int(row[3] or 0),
        "in_run": int(row[4] or 0),
        "other_currencies": sorted(others),
    }


async def _recon_summary(db: AsyncSession, org_id: str) -> dict:
    counts = {"unmatched": 0, "matched": 0, "ignored": 0}
    rows = await db.execute(
        select(BankLine.status, func.count(BankLine.id))
        .where(BankLine.org_id == org_id)
        .group_by(BankLine.status)
    )
    for status, n in rows:
        if status in counts:
            counts[status] = int(n)
    unmatched_amount = await db.scalar(
        select(func.coalesce(func.sum(func.abs(BankLine.amount)), 0)).where(
            BankLine.org_id == org_id, BankLine.status == "unmatched"
        )
    )
    return {
        **counts,
        "unmatched_amount": money.q2(Decimal(unmatched_amount or 0)),
    }


async def summary(db: AsyncSession, org_id: str, today: date | None = None) -> dict:
    """The full cash-position roll-up (AR + AP + reconciliation + net)."""
    today = today or date.today()
    ar = await _ar_summary(db, org_id, today)
    ap = await _ap_summary(db, org_id, today, ar["currency"])
    recon = await _recon_summary(db, org_id)
    net = money.q2(Decimal(ar["outstanding"]) - Decimal(ap["outstanding"]))
    return {
        "currency": ar["currency"],
        "receivables": ar,
        "payables": ap,
        "reconciliation": recon,
        "net_position": net,
    }
