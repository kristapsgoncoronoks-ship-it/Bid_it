"""Postgres-only proof that two truly-concurrent pays of the SAME payment run
settle it exactly once (WO-9, invariant §4.13).

The guarantee rests on the route loading the run `SELECT … FOR UPDATE` before
`mark_paid`, so overlapping pay transactions serialize on the run row: the loser
re-reads a `paid` run after the winner commits and is refused by the single-shot
state gate — its transaction changes NOTHING (no second ledger entry, no double
`amount_paid`). That lock is a genuine row lock only on Postgres (the SQLite test
harness runs one shared connection), so this test SKIPS on the default suite and
runs in the Postgres CI job via `RLS_TEST_DATABASE_URL` — mirroring
tests/test_numbering_concurrency.py.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenant import reset_current_org, set_current_org
from app.models.invoice import Invoice, InvoiceStatus, WorkflowState
from app.models.organization import Organization
from app.models.payment_run import RUN_APPROVED, RUN_PAID, PaymentRun
from app.models.supplier_payment import SupplierPayment
from app.models.vendor import Vendor
from app.services import payment_run as pr

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL,
    reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the concurrency test",
)


@pg_only
@pytest.mark.asyncio
async def test_two_concurrent_pays_settle_once():
    engine = create_async_engine(PG_URL, pool_size=6, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    org_id = str(uuid.uuid4())
    try:
        async with sm() as s:
            s.add(Organization(id=org_id, name="Pay Race Co"))
            await s.commit()

        tok = set_current_org(org_id)
        try:
            # Seed: vendor → scheduled invoice → an APPROVED run holding it.
            async with sm() as s:
                vendor = Vendor(org_id=org_id, name="Race Supplies BV")
                s.add(vendor)
                await s.flush()
                inv = Invoice(
                    org_id=org_id,
                    vendor_id=vendor.id,
                    invoice_number="INV-RACE-1",
                    issue_date=date(2026, 5, 1),
                    due_date=date(2026, 12, 1),
                    currency="EUR",
                    subtotal=Decimal("100.00"),
                    tax_amount=Decimal("0.00"),
                    total=Decimal("100.00"),
                    total_eur=Decimal("100.00"),
                    status=InvoiceStatus.approved,
                    workflow_state=WorkflowState.scheduled_for_payment,
                )
                s.add(inv)
                await s.flush()
                run = PaymentRun(
                    org_id=org_id,
                    method="bank_transfer",
                    status=RUN_APPROVED,
                    total_eur=Decimal("100.00"),
                    created_by="maker@race.io",
                    approved_by="checker@race.io",
                )
                s.add(run)
                await s.flush()
                inv.payment_run_id = run.id
                run_id, inv_id = run.id, inv.id
                await s.commit()

            barrier = asyncio.Barrier(2)
            results: list[str] = []

            async def pay(worker: str) -> None:
                await barrier.wait()  # maximize the overlap
                async with sm() as s:
                    row = await s.scalar(
                        select(PaymentRun)
                        .where(PaymentRun.id == run_id, PaymentRun.org_id == org_id)
                        .with_for_update()  # the route's lock — the control under test
                    )
                    try:
                        await pr.mark_paid(
                            s,
                            org_id,
                            row,
                            reference=f"RACE-{worker}",
                            actor_id=str(uuid.uuid4()),
                            actor_email=f"payer-{worker}@race.io",
                        )
                        await s.commit()
                        results.append(f"paid:{worker}")
                    except pr.PaymentRunError:
                        await s.rollback()
                        results.append(f"refused:{worker}")

            await asyncio.gather(pay("a"), pay("b"))

            # Exactly one winner; the loser's state is unchanged, not partially applied.
            assert sorted(r.split(":")[0] for r in results) == ["paid", "refused"]
            async with sm() as s:
                run_row = await s.get(PaymentRun, run_id)
                assert run_row is not None and run_row.status == RUN_PAID
                inv_row = await s.get(Invoice, inv_id)
                assert inv_row is not None
                assert inv_row.workflow_state == WorkflowState.paid
                assert Decimal(inv_row.amount_paid) == Decimal("100.00")
                # §4.11: SUM(ledger) == amount_paid — ONE settlement entry, once.
                n_entries = await s.scalar(
                    select(func.count())
                    .select_from(SupplierPayment)
                    .where(SupplierPayment.invoice_id == inv_id)
                )
                total_ledger = await s.scalar(
                    select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
                        SupplierPayment.invoice_id == inv_id
                    )
                )
                assert n_entries == 1
                assert Decimal(total_ledger) == Decimal("100.00")
        finally:
            reset_current_org(tok)
    finally:
        await engine.dispose()
