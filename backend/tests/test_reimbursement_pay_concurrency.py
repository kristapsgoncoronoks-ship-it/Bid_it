"""Postgres-only proof that two truly-concurrent pays of the SAME reimbursement
batch settle it exactly once (WO-Y).

WHY THIS EXISTS
---------------
`routes/reimbursements.py::pay_batch` loads the batch `SELECT … FOR UPDATE`
before `mark_paid`, with a comment stating that the lock serialises overlapping
payouts. Nothing tested it. Its twin on the supplier side —
`test_payment_run_pay_concurrency.py` — has carried that proof since WO-9, so
the employee payout rail was the one settlement path in the product whose lock
was an assertion in a comment.

That asymmetry matters more than it looks: this is money leaving the company to
a named person. A second settlement would flip already-`reimbursed` reports a
second time and stamp a second reference over the first, and the batch's own
`version` counter — the optimistic guard the route checks — would have been
bumped twice with only one of the two payments recorded anywhere a human reads.

WHAT IT PROVES
--------------
Two overlapping transactions, released together by a barrier, both take the
route's `with_for_update()` on the batch row. Exactly one pays. The loser
re-reads a `paid` batch after the winner commits and is refused by the
single-shot state gate in `mark_paid`, so its transaction changes NOTHING: no
second reference, no second `version` bump, no report re-stamped.

The lock is a genuine row lock only on Postgres — the SQLite test harness runs
one shared connection, so a SQLite run would pass whether or not the lock were
there, which is worse than not running. It therefore skips on the default suite
and runs in the Postgres CI job via `RLS_TEST_DATABASE_URL`, mirroring
`test_payment_run_pay_concurrency.py` and `test_numbering_concurrency.py`.

Segregation of duties is satisfied deliberately rather than bypassed: the batch
has a maker, and both racers are third parties. A test that tripped the SoD
refusal would report "refused" for the loser while proving nothing about the
lock — the two failures look identical from the outside and only one of them is
the control under test.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenant import reset_current_org, set_current_org
from app.models.expense import BATCH_OPEN, BATCH_PAID, ExpenseReport, ReimbursementBatch
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.services import reimbursement

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL,
    reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the concurrency test",
)


@pg_only
@pytest.mark.asyncio
async def test_two_concurrent_pays_settle_one_batch_once():
    engine = create_async_engine(PG_URL, pool_size=6, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    org_id = str(uuid.uuid4())
    stamp = uuid.uuid4().hex[:8]
    try:
        async with sm() as s:
            s.add(Organization(id=org_id, name="Payout Race Co"))
            await s.commit()

        tok = set_current_org(org_id)
        try:
            # Seed: an employee with an approved report, held by an OPEN batch
            # whose maker is a third user (so neither racer trips SoD).
            async with sm() as s:
                employee = User(
                    org_id=org_id,
                    email=f"driver-{stamp}@race.io",
                    name="Site Crew Member",
                    hashed_password="x",
                    role=UserRole.user,
                )
                maker = User(
                    org_id=org_id,
                    email=f"maker-{stamp}@race.io",
                    name="Batch Maker",
                    hashed_password="x",
                    role=UserRole.admin,
                )
                s.add_all([employee, maker])
                await s.flush()
                report = ExpenseReport(
                    org_id=org_id,
                    employee_id=employee.id,
                    employee_name=employee.name,
                    title="Fuel and tolls, June",
                    status="marked_for_reimbursement",
                    currency="EUR",
                    total=Decimal("240.00"),
                    vat_total=Decimal("0.00"),
                    total_eur=Decimal("240.00"),
                    decided_at=datetime.now(UTC),
                    decided_by=maker.email,
                )
                s.add(report)
                await s.flush()
                batch = ReimbursementBatch(
                    org_id=org_id,
                    method="bank_transfer",
                    status=BATCH_OPEN,
                    total_eur=Decimal("240.00"),
                    created_by=maker.email,
                    created_by_id=maker.id,
                )
                s.add(batch)
                await s.flush()
                report.payout_batch_id = batch.id
                batch_id, report_id = batch.id, report.id
                await s.commit()

            barrier = asyncio.Barrier(2)
            results: list[str] = []

            async def pay(worker: str) -> None:
                await barrier.wait()  # maximize the overlap
                async with sm() as s:
                    row = await s.scalar(
                        select(ReimbursementBatch)
                        .where(
                            ReimbursementBatch.id == batch_id,
                            ReimbursementBatch.org_id == org_id,
                        )
                        .with_for_update()  # the route's lock — the control under test
                    )
                    try:
                        await reimbursement.mark_paid(
                            s,
                            org_id,
                            row,
                            reference=f"PAYOUT-{worker}",
                            actor_id=str(uuid.uuid4()),
                            actor_email=f"payer-{worker}@race.io",
                        )
                        await s.commit()
                        results.append(f"paid:{worker}")
                    except reimbursement.ReimbursementError:
                        await s.rollback()
                        results.append(f"refused:{worker}")

            await asyncio.gather(pay("a"), pay("b"))

            # Exactly one winner, and the loser was refused rather than crashing.
            assert sorted(r.split(":")[0] for r in results) == ["paid", "refused"]
            winner = next(r.split(":")[1] for r in results if r.startswith("paid:"))

            async with sm() as s:
                paid = await s.get(ReimbursementBatch, batch_id)
                assert paid is not None
                assert paid.status == BATCH_PAID
                # The reference is the WINNER's, whole — not the loser's, and not
                # one overwritten by the other.
                assert paid.reference == f"PAYOUT-{winner}"
                # ONE settlement means ONE version bump. Two would mean the
                # loser's transaction had also written, and the optimistic guard
                # the route relies on would be counting payments that left no
                # other trace.
                assert paid.version == 2
                assert paid.paid_at is not None

                report_row = await s.get(ExpenseReport, report_id)
                assert report_row is not None
                assert report_row.status == "reimbursed"
                assert report_row.payment_reference == f"PAYOUT-{winner}"
                # …and the batch still holds exactly the one report it started
                # with: the loser neither re-stamped it nor unlinked it.
                assert (
                    await s.scalar(
                        select(func.count())
                        .select_from(ExpenseReport)
                        .where(ExpenseReport.payout_batch_id == batch_id)
                    )
                    == 1
                )
        finally:
            reset_current_org(tok)
    finally:
        await engine.dispose()


@pg_only
@pytest.mark.asyncio
async def test_a_cancel_racing_a_pay_cannot_unmake_the_payment():
    """The defect WO-Y found while building the proof above.

    `_load` has documented its lock as serialising "pay/cancel" since it was
    written, and the pay route took it — the cancel route did not. That gap is
    not benign, because a plain read does not block: cancel decided "still open"
    in Python, then its UPDATE waited on the payer's row lock, and after the
    payment committed it wrote `cancelled` straight over it and unlinked the
    reports. The result is the worst kind of inconsistency in a payout system —
    reports stamped `reimbursed` with a real bank reference, belonging to a
    batch that says it was never run.

    With both paths locking, the cancel re-reads a `paid` batch and is refused
    by `cancel_batch`'s own state gate.
    """
    engine = create_async_engine(PG_URL, pool_size=6, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    org_id = str(uuid.uuid4())
    stamp = uuid.uuid4().hex[:8]
    try:
        async with sm() as s:
            s.add(Organization(id=org_id, name="Cancel Race Co"))
            await s.commit()

        tok = set_current_org(org_id)
        try:
            async with sm() as s:
                employee = User(
                    org_id=org_id,
                    email=f"crew-{stamp}@race.io",
                    name="Site Crew Member",
                    hashed_password="x",
                    role=UserRole.user,
                )
                s.add(employee)
                await s.flush()
                report = ExpenseReport(
                    org_id=org_id,
                    employee_id=employee.id,
                    employee_name=employee.name,
                    title="Depot parking, June",
                    status="marked_for_reimbursement",
                    currency="EUR",
                    total=Decimal("60.00"),
                    vat_total=Decimal("0.00"),
                    total_eur=Decimal("60.00"),
                )
                s.add(report)
                await s.flush()
                batch = ReimbursementBatch(
                    org_id=org_id,
                    method="bank_transfer",
                    status=BATCH_OPEN,
                    total_eur=Decimal("60.00"),
                    created_by=f"maker-{stamp}@race.io",
                    created_by_id=str(uuid.uuid4()),
                )
                s.add(batch)
                await s.flush()
                report.payout_batch_id = batch.id
                batch_id, report_id = batch.id, report.id
                await s.commit()

            barrier = asyncio.Barrier(2)
            outcome: dict[str, str] = {}

            async def locked_batch(s):
                return await s.scalar(
                    select(ReimbursementBatch)
                    .where(
                        ReimbursementBatch.id == batch_id,
                        ReimbursementBatch.org_id == org_id,
                    )
                    .with_for_update()  # BOTH routes take it — that is the fix
                )

            async def payer() -> None:
                await barrier.wait()
                async with sm() as s:
                    row = await locked_batch(s)
                    try:
                        await reimbursement.mark_paid(
                            s,
                            org_id,
                            row,
                            reference="PAYOUT-REAL",
                            actor_id=str(uuid.uuid4()),
                            actor_email=f"payer-{stamp}@race.io",
                        )
                        await s.commit()
                        outcome["pay"] = "paid"
                    except reimbursement.ReimbursementError:
                        await s.rollback()
                        outcome["pay"] = "refused"

            async def canceller() -> None:
                await barrier.wait()
                async with sm() as s:
                    row = await locked_batch(s)
                    try:
                        await reimbursement.cancel_batch(s, org_id, row)
                        await s.commit()
                        outcome["cancel"] = "cancelled"
                    except reimbursement.ReimbursementError:
                        await s.rollback()
                        outcome["cancel"] = "refused"

            await asyncio.gather(payer(), canceller())

            async with sm() as s:
                final = await s.get(ReimbursementBatch, batch_id)
                report_row = await s.get(ExpenseReport, report_id)
                assert final is not None and report_row is not None

                # Whichever won, the batch and its report agree with each other.
                # That agreement is the whole invariant: no state where money is
                # recorded as sent against a batch that says it never ran.
                if outcome["pay"] == "paid":
                    assert outcome["cancel"] == "refused"
                    assert final.status == BATCH_PAID
                    assert report_row.status == "reimbursed"
                    assert report_row.payout_batch_id == batch_id
                    assert report_row.payment_reference == "PAYOUT-REAL"
                else:
                    assert outcome["cancel"] == "cancelled"
                    assert final.status == "cancelled"
                    # Released back to the payable pool, unpaid and unstamped.
                    assert report_row.payout_batch_id is None
                    assert report_row.status == "marked_for_reimbursement"
                    assert report_row.payment_reference is None
        finally:
            reset_current_org(tok)
    finally:
        await engine.dispose()
