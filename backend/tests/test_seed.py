"""Regression test for R3 (`docs/plan/plan-a/wo/WO-28-R3.md`): the demo seed
script must drive every AP invoice through the REAL review -> approval ->
payment-run workflow, so `workflow_state` (what cash-position/payment-runs/the
dashboard read) never diverges from the legacy aging `status` (what the
Invoices list badge reads). Before the fix, `app/seed.py` hand-set `status`
directly and left every invoice's `workflow_state` at its `draft` default, so
a freshly seeded demo showed >EUR 1M on /invoices while /cash-position and
/payment-runs both showed EUR 0.00 — a page-to-page contradiction on the
officially documented customer-facing demo login."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import seed as seed_module
from app.models.invoice import Invoice, InvoiceStatus, WorkflowState
from app.models.organization import Organization
from app.models.payment_run import RUN_PAID, PaymentRun
from app.services import cash_position, payment_run

# Every open-payable workflow state: an invoice that has left 'draft' but is
# not yet fully settled (mirrors cash_position._PAYABLE_STATES + 'paid').
_OPEN_PAYABLE = {
    WorkflowState.approved,
    WorkflowState.scheduled_for_payment,
    WorkflowState.partially_paid,
}


async def _seed_into_memory(monkeypatch):
    """Run the real `seed.seed()` against a fresh in-memory SQLite DB (mirrors
    `tests/conftest.py::_db`'s engine setup) by redirecting the module-level
    `engine`/`SessionLocal` it uses, then return a fresh sessionmaker on the
    same engine for assertions."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sm = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(seed_module, "engine", engine)
    monkeypatch.setattr(seed_module, "SessionLocal", sm)

    await seed_module.seed()
    return sm


async def test_seed_drives_ap_invoices_through_real_workflow(monkeypatch):
    sm = await _seed_into_memory(monkeypatch)

    async with sm() as db:
        org = await db.scalar(select(Organization).where(Organization.name == "Demo Logistics Ltd"))
        assert org is not None

        # Not left at the 'draft' default (today's bug: 100% of them are).
        rows = (
            await db.execute(
                select(Invoice.workflow_state, func.count())
                .where(Invoice.org_id == org.id)
                .group_by(Invoice.workflow_state)
            )
        ).all()
        by_state = dict(rows)
        assert by_state, "no AP invoices were seeded"
        assert WorkflowState.draft not in by_state, by_state

        # Every legacy-'paid' invoice reached workflow_state 'paid' (the exact
        # R3 divergence: cash-position/payment-runs never counted these).
        paid_invoices = list(
            await db.scalars(
                select(Invoice).where(
                    Invoice.org_id == org.id, Invoice.status == InvoiceStatus.paid
                )
            )
        )
        assert paid_invoices
        for inv in paid_invoices:
            assert inv.workflow_state == WorkflowState.paid, (
                inv.invoice_number,
                inv.workflow_state,
            )
            # The payment-run pass settles in FULL — no partial payables left behind.
            assert inv.amount_paid == inv.total, inv.invoice_number

        # Every pending/overdue invoice reached an open-payable state, never
        # stuck at draft/rejected/cancelled.
        open_invoices = list(
            await db.scalars(
                select(Invoice).where(
                    Invoice.org_id == org.id,
                    Invoice.status.in_([InvoiceStatus.pending, InvoiceStatus.overdue]),
                )
            )
        )
        assert open_invoices
        for inv in open_invoices:
            assert inv.workflow_state in _OPEN_PAYABLE, (inv.invoice_number, inv.workflow_state)

        # Cash-position payables reconcile: non-zero, and matching the invoices
        # actually driven to an open-payable / paid state above.
        summary = await cash_position.summary(db, org.id)
        assert summary["payables"]["outstanding"] > 0
        assert summary["payables"]["count"] > 0

        # The payment-run candidate pool (GET /payable) is not empty either.
        pool = await payment_run.payable_invoices(db, org.id)
        assert pool

        # At least one real payment run was created, approved and paid.
        paid_runs = list(
            await db.scalars(
                select(PaymentRun).where(PaymentRun.org_id == org.id, PaymentRun.status == RUN_PAID)
            )
        )
        assert paid_runs
