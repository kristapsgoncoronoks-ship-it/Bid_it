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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.bank_import import BankLine
from app.models.invoice import Invoice, WorkflowState
from app.services import ap_status, issued_reports

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
    rep = await issued_reports.receivables(db, org_id, None, None, None, today=today)
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


async def _ap_summary(db: AsyncSession, org_id: str, today: date) -> dict:
    invoices = list(
        await db.scalars(
            select(Invoice).where(
                Invoice.org_id == org_id, Invoice.workflow_state.in_(_PAYABLE_STATES)
            )
        )
    )
    outstanding = _ZERO
    overdue = _ZERO
    scheduled = 0
    in_run = 0
    for inv in invoices:
        out = ap_status.outstanding_of(inv)
        outstanding += out
        if ap_status.status_of(inv, today) == ap_status.OVERDUE:
            overdue += out
        if inv.workflow_state == WorkflowState.scheduled_for_payment:
            scheduled += 1
        if inv.payment_run_id:
            in_run += 1
    return {
        "outstanding": money.q2(outstanding),
        "overdue": money.q2(overdue),
        "count": len(invoices),
        "scheduled": scheduled,
        "in_run": in_run,
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
    ap = await _ap_summary(db, org_id, today)
    recon = await _recon_summary(db, org_id)
    net = money.q2(Decimal(ar["outstanding"]) - Decimal(ap["outstanding"]))
    return {
        "currency": ar["currency"],
        "receivables": ar,
        "payables": ap,
        "reconciliation": recon,
        "net_position": net,
    }
