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

EX19 additionally exercises the actual HTTP route transactions for CSV/cancel,
SEPA/cancel and pay/cancel in both commit orders. The original route loader
takes every tested lock; a separate observer proves PostgreSQL blocked the
second requester, then committed column/audit/outbox snapshots prove its
refusal made no writes.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import reimbursements as routes
from app.core.database import get_session
from app.core.tenant import reset_current_org, set_current_org
from app.main import app
from app.models.audit import AuditEvent
from app.models.expense import (
    BATCH_OPEN,
    BATCH_PAID,
    ExpenseCommand,
    ExpenseReport,
    ReimbursementBatch,
)
from app.models.issuer import IssuerProfile
from app.models.job import Job
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.services import audit, expense_commands, reimbursement

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


async def _create_command_state(factory, org_id):
    """Committed whole-row state, including command/audit/outbox side effects."""
    async with factory() as session:
        return {
            model.__tablename__: [
                dict(row)
                for row in (
                    await session.execute(
                        select(model.__table__).where(model.org_id == org_id).order_by(model.id)
                    )
                ).mappings()
            ]
            for model in (
                ReimbursementBatch,
                ExpenseReport,
                ExpenseCommand,
                AuditEvent,
                WebhookDelivery,
                Job,
            )
        }


@pg_only
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schedule", ["same_order", "opposite_order", "partial_overlap", "same_key"]
)
@pytest.mark.parametrize("round_number", [1, 2])
async def test_create_commands_serialize_original_route_transactions(
    monkeypatch, record_property, schedule, round_number
):
    """EX18: two actual HTTP transactions contend before allocating any batch.

    A wrapper pauses only after the production report loader holds its locks.
    It never inserts a lock. Different actors overlap at the reports; same
    actor/key requests overlap at the shared membership lock before receipt
    lookup. A third connection proves the second PID is actually blocked, then
    every committed column is compared before/after the loser and its retry.
    Repeating every controlled schedule gives an explicit non-timing proof.
    """
    from time import perf_counter

    engine = create_async_engine(PG_URL, pool_size=6, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _get_test_session():
        async with sm() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    original_reports = reimbursement._locked_reports
    original_actor = expense_commands.lock_actor
    request_actor = ContextVar("ex18_request_actor", default=None)
    first_locked = asyncio.Event()
    second_entered = asyncio.Event()
    second_loaded = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    pids = {}
    tasks = []
    started = perf_counter()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            stamp = uuid.uuid4().hex[:12]
            registered = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": f"EX18 route schedules {stamp}",
                    "name": "Synthetic batch maker",
                    "email": f"ex18-maker-{stamp}@example.com",
                    "password": "supersecret",
                },
            )
            assert registered.status_code == 201, registered.text
            identity = registered.json()
            org_id = identity["user"]["org_id"]
            first_headers = {"Authorization": f"Bearer {identity['token']['access_token']}"}
            client.headers.update(first_headers)
            activated = await client.put("/api/v1/modules/expenses", json={"enabled": True})
            assert activated.status_code == 200, activated.text
            invitation = await client.post(
                "/api/v1/team/invites",
                json={"email": f"ex18-second-{stamp}@example.com", "role": "admin"},
            )
            assert invitation.status_code == 201, invitation.text
            accepted = await client.post(
                "/api/v1/auth/accept-invite",
                json={
                    "token": invitation.json()["token"],
                    "name": "Synthetic second maker",
                    "password": "supersecret",
                },
            )
            assert accepted.status_code == 201, accepted.text
            second_headers = (
                first_headers
                if schedule == "same_key"
                else {"Authorization": f"Bearer {accepted.json()['token']['access_token']}"}
            )
            async with sm() as session:
                claimant = User(
                    org_id=org_id,
                    email=f"ex18-claimant-{stamp}@example.com",
                    name="Synthetic claimant",
                    hashed_password="fixture-only",
                    role=UserRole.user,
                )
                session.add(claimant)
                await session.flush()
                reports = [
                    ExpenseReport(
                        org_id=org_id,
                        employee_id=claimant.id,
                        employee_name=claimant.name,
                        title=f"Synthetic EX18 report {index}",
                        status="approved",
                        currency="EUR",
                        total=Decimal("0.10"),
                        total_eur=Decimal("0.10"),
                        vat_total=Decimal("0"),
                        version=7,
                    )
                    for index in range(3)
                ]
                session.add_all(reports)
                session.add(
                    WebhookEndpoint(
                        org_id=org_id,
                        url="https://example.test/ex18-hook",
                        secret="synthetic-secret",
                        events="expense.reimbursement_created",
                    )
                )
                await session.flush()
                report_ids = [row.id for row in reports]
                await session.commit()

            async def scheduled_actor(db, current, **kwargs):
                actor = request_actor.get()
                if schedule == "same_key" and actor == "second":
                    pids["second"] = await db.scalar(text("SELECT pg_backend_pid()"))
                    second_entered.set()
                await original_actor(db, current, **kwargs)
                if schedule == "same_key" and actor == "second":
                    second_loaded.set()
                    await release_second.wait()

            async def scheduled_reports(db, requested_org, selected_ids):
                actor = request_actor.get()
                if actor == "second":
                    pids["second"] = await db.scalar(text("SELECT pg_backend_pid()"))
                    second_entered.set()
                rows = await original_reports(db, requested_org, selected_ids)
                if actor == "first":
                    pids["first"] = await db.scalar(text("SELECT pg_backend_pid()"))
                    first_locked.set()
                    await release_first.wait()
                elif actor == "second":
                    second_loaded.set()
                    await release_second.wait()
                return rows

            monkeypatch.setattr(expense_commands, "lock_actor", scheduled_actor)
            monkeypatch.setattr(reimbursement, "_locked_reports", scheduled_reports)
            first_ids = report_ids[:2]
            if schedule == "opposite_order":
                second_ids = list(reversed(first_ids))
            elif schedule == "partial_overlap":
                second_ids = report_ids[1:]
            else:
                second_ids = first_ids
            first_key = str(uuid.uuid4())
            second_key = first_key if schedule == "same_key" else str(uuid.uuid4())

            async def request(actor=None):
                role = actor or "second"
                selected = first_ids if role == "first" else second_ids
                key = first_key if role == "first" else second_key
                headers = first_headers if role == "first" else second_headers
                token = request_actor.set(actor)
                try:
                    return await client.post(
                        "/api/v1/reimbursements",
                        headers={**headers, "Idempotency-Key": key},
                        json={
                            "report_ids": selected,
                            "report_versions": dict.fromkeys(selected, 7),
                        },
                    )
                finally:
                    request_actor.reset(token)

            tasks.append(asyncio.create_task(request("first"), name="ex18-first"))
            await asyncio.wait_for(first_locked.wait(), timeout=10)
            tasks.append(asyncio.create_task(request("second"), name="ex18-second"))
            await asyncio.wait_for(second_entered.wait(), timeout=10)
            assert pids["first"] != pids["second"]
            observations = 0

            async def observe_wait():
                nonlocal observations
                async with sm() as observer:
                    while True:
                        observations += 1
                        blocked = await observer.scalar(
                            text(
                                "SELECT CAST(:holder AS integer) = ANY(pg_blocking_pids(CAST(:waiter AS integer)))"
                            ),
                            {"holder": pids["first"], "waiter": pids["second"]},
                        )
                        if blocked:
                            return
                        assert not second_loaded.is_set(), (
                            "second create read the selection before the first transaction committed"
                        )
                        assert not tasks[1].done(), (
                            "second create did not wait for the original lock"
                        )
                        await asyncio.sleep(0.01)

            await asyncio.wait_for(observe_wait(), timeout=10)
            release_first.set()
            winner = await asyncio.wait_for(tasks[0], timeout=15)
            assert winner.status_code == 201, winner.text
            await asyncio.wait_for(second_loaded.wait(), timeout=10)
            committed = await _create_command_state(sm, org_id)
            release_second.set()
            loser = await asyncio.wait_for(tasks[1], timeout=15)
            if schedule == "same_key":
                assert loser.status_code == 201 and loser.json() == winner.json(), loser.text
            else:
                assert loser.status_code == 409, loser.text
                assert loser.json()["code"] == "reimbursement_selection_conflict"
            assert await _create_command_state(sm, org_id) == committed
            repeat = await request()
            assert repeat.status_code == loser.status_code, repeat.text
            if schedule == "same_key":
                assert repeat.json() == winner.json()
            assert await _create_command_state(sm, org_id) == committed
            assert (
                len(committed["reimbursement_batches"]) == len(committed["expense_commands"]) == 1
            )
            assert len(committed["webhook_deliveries"]) == len(committed["jobs"]) == 1
            actions = [
                row for row in committed["audit_events"] if row["action"] == audit.A.REIMBURSE_BATCH
            ]
            assert len(actions) == 1
            batch_id = winner.json()["id"]
            assert winner.json()["total_eur"] == "0.20"
            assert winner.json()["report_count"] == 2
            for report in committed["expense_reports"]:
                selected = report["id"] in first_ids
                assert report["payout_batch_id"] == (batch_id if selected else None)
                assert report["version"] == (8 if selected else 7)
            record_property("schedule", schedule)
            record_property("repeat_round", round_number)
            record_property("distinct_database_pids", True)
            record_property("original_lock_block_observed", True)
            record_property("wait_observation_queries", observations)
            record_property("elapsed_seconds", round(perf_counter() - started, 6))
    finally:
        release_first.set()
        release_second.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        app.dependency_overrides.clear()
        await engine.dispose()


@pg_only
@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["role", "report_version"])
async def test_create_rechecks_authority_and_version_after_actual_lock_wait(monkeypatch, changed):
    """A request's pre-wait view cannot authorize a post-wait financial write."""
    engine = create_async_engine(PG_URL, pool_size=5, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _get_test_session():
        async with sm() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    entered = asyncio.Event()
    task = None
    pids = {}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            stamp = uuid.uuid4().hex[:12]
            registered = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": f"EX18 post-wait {stamp}",
                    "name": "Synthetic maker",
                    "email": f"ex18-wait-{stamp}@example.com",
                    "password": "supersecret",
                },
            )
            assert registered.status_code == 201, registered.text
            identity = registered.json()
            org_id = identity["user"]["org_id"]
            actor_id = identity["user"]["id"]
            client.headers["Authorization"] = f"Bearer {identity['token']['access_token']}"
            assert (
                await client.put("/api/v1/modules/expenses", json={"enabled": True})
            ).status_code == 200
            async with sm() as setup:
                report = ExpenseReport(
                    org_id=org_id,
                    employee_id=actor_id,
                    employee_name="Synthetic claimant",
                    title="Synthetic observed report",
                    status="approved",
                    currency="EUR",
                    total=Decimal("1.00"),
                    vat_total=Decimal("0.00"),
                    version=7,
                )
                setup.add(report)
                await setup.flush()
                report_id = report.id
                await setup.commit()
            before = await _create_command_state(sm, org_id)
            async with sm() as blocker:
                pids["holder"] = await blocker.scalar(text("SELECT pg_backend_pid()"))
                if changed == "role":
                    row = await blocker.scalar(
                        select(Membership)
                        .where(Membership.org_id == org_id, Membership.user_id == actor_id)
                        .with_for_update()
                    )
                    original = expense_commands.lock_actor

                    async def at_actor(db, current, **kwargs):
                        pids["waiter"] = await db.scalar(text("SELECT pg_backend_pid()"))
                        entered.set()
                        return await original(db, current, **kwargs)

                    monkeypatch.setattr(expense_commands, "lock_actor", at_actor)
                else:
                    row = await blocker.scalar(
                        select(ExpenseReport)
                        .where(ExpenseReport.org_id == org_id, ExpenseReport.id == report_id)
                        .with_for_update()
                    )
                    original = reimbursement._locked_reports

                    async def at_reports(db, requested_org, selected_ids):
                        pids["waiter"] = await db.scalar(text("SELECT pg_backend_pid()"))
                        entered.set()
                        return await original(db, requested_org, selected_ids)

                    monkeypatch.setattr(reimbursement, "_locked_reports", at_reports)
                task = asyncio.create_task(
                    client.post(
                        "/api/v1/reimbursements",
                        headers={"Idempotency-Key": str(uuid.uuid4())},
                        json={"report_ids": [report_id], "report_versions": {report_id: 7}},
                    )
                )
                await asyncio.wait_for(entered.wait(), timeout=10)
                assert pids["holder"] != pids["waiter"]

                async def observe_wait():
                    async with sm() as observer:
                        while not await observer.scalar(
                            text(
                                "SELECT CAST(:holder AS integer) = ANY(pg_blocking_pids(CAST(:waiter AS integer)))"
                            ),
                            pids,
                        ):
                            assert not task.done(), "create did not await the authoritative row"
                            await asyncio.sleep(0.01)

                await asyncio.wait_for(observe_wait(), timeout=10)
                if changed == "role":
                    row.role = UserRole.user
                    row.is_expense_approver = False
                else:
                    row.version += 1
                await blocker.commit()
                refused = await asyncio.wait_for(task, timeout=15)
            assert refused.status_code == (403 if changed == "role" else 409), refused.text
            if changed == "report_version":
                assert refused.json()["code"] == "reimbursement_selection_conflict"
            after = await _create_command_state(sm, org_id)
            if changed == "report_version":
                # The fixture's deliberate concurrent edit is the only report
                # change. TimestampMixin advances updated_at with that edit.
                expected = before["expense_reports"][0]
                expected["version"] = 8
                expected["updated_at"] = after["expense_reports"][0]["updated_at"]
            assert after == before
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        app.dependency_overrides.clear()
        await engine.dispose()


async def _route_state(sm, org_id, batch_id, report_id):
    """Read committed columns, bypassing the identity map of either requester."""
    async with sm() as session:
        batch = dict(
            (
                await session.execute(
                    select(ReimbursementBatch.__table__).where(
                        ReimbursementBatch.org_id == org_id, ReimbursementBatch.id == batch_id
                    )
                )
            )
            .mappings()
            .one()
        )
        report = dict(
            (
                await session.execute(
                    select(ExpenseReport.__table__).where(
                        ExpenseReport.org_id == org_id, ExpenseReport.id == report_id
                    )
                )
            )
            .mappings()
            .one()
        )
        actions = list(
            await session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.org_id == org_id, AuditEvent.target_id == batch_id)
                .order_by(AuditEvent.seq)
            )
        )
        deliveries = set(
            await session.scalars(
                select(WebhookDelivery.id).where(WebhookDelivery.org_id == org_id)
            )
        )
        jobs = set(await session.scalars(select(Job.id).where(Job.org_id == org_id)))
        return batch, report, actions, deliveries, jobs


@pg_only
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("csv", "cancel"),
        ("cancel", "csv"),
        ("sepa", "cancel"),
        ("cancel", "sepa"),
        ("pay", "cancel"),
        ("cancel", "pay"),
    ],
)
async def test_route_transactions_serialize_export_pay_and_cancel(monkeypatch, first, second):
    """EX19: exercise the real route locks, in both deterministic commit orders.

    The scheduling wrapper only pauses AFTER the route's original `_load` has
    executed. It neither inserts a lock nor substitutes a service call. A third
    connection observes pg_blocking_pids before releasing the winner, so a test
    cannot pass by accidentally running the requests sequentially. The losing
    request must leave every persisted batch/report column and side effect as
    the winning request committed it.
    """
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _get_test_session():
        async with sm() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    first_loaded = asyncio.Event()
    second_entered = asyncio.Event()
    second_loaded = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    request_actor = ContextVar("ex19_request_actor", default=None)
    pids = {}
    original_load = routes._load
    tasks = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            stamp = uuid.uuid4().hex[:12]
            registered = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": f"EX19 route schedules {stamp}",
                    "name": "Checker",
                    "email": f"checker-{stamp}@example.com",
                    "password": "supersecret",
                },
            )
            assert registered.status_code == 201, registered.text
            org_id = registered.json()["user"]["org_id"]
            client.headers["Authorization"] = f"Bearer {registered.json()['token']['access_token']}"
            activated = await client.put("/api/v1/modules/expenses", json={"enabled": True})
            assert activated.status_code == 200, activated.text

            # Fixture is a previously approved report held by an OPEN batch.
            # Its maker differs from the authenticated checker: a pay refusal
            # must prove the state conflict, never an incidental SoD failure.
            async with sm() as session:
                maker = User(
                    org_id=org_id,
                    email=f"maker-{stamp}@example.com",
                    name="Synthetic Payee and Maker",
                    hashed_password="fixture-only",
                    role=UserRole.admin,
                    iban="DE89370400440532013000",
                    bic="COBADEFFXXX",
                )
                session.add(maker)
                await session.flush()
                session.add(
                    Membership(
                        org_id=org_id,
                        user_id=maker.id,
                        role=UserRole.admin,
                        email=maker.email,
                        name=maker.name,
                    )
                )
                session.add(
                    IssuerProfile(
                        org_id=org_id,
                        legal_name="Synthetic EX19 BV",
                        is_default=True,
                        iban="NL91ABNA0417164300",
                        bic="ABNANL2A",
                    )
                )
                session.add(
                    WebhookEndpoint(
                        org_id=org_id,
                        url="https://example.test/ex19-hook",
                        secret="synthetic-test-secret",
                        events="*",
                    )
                )
                batch = ReimbursementBatch(
                    org_id=org_id,
                    method="bank_transfer",
                    status=BATCH_OPEN,
                    total_eur=Decimal("60.00"),
                    created_by=maker.email,
                    created_by_id=maker.id,
                )
                session.add(batch)
                await session.flush()
                report = ExpenseReport(
                    org_id=org_id,
                    employee_id=maker.id,
                    employee_name=maker.name,
                    title="Synthetic depot parking",
                    status="marked_for_reimbursement",
                    currency="EUR",
                    total=Decimal("60.00"),
                    vat_total=Decimal("0.00"),
                    total_eur=Decimal("60.00"),
                    payout_batch_id=batch.id,
                )
                session.add(report)
                await session.flush()
                batch_id, report_id = batch.id, report.id
                await session.commit()

            async def scheduled_load(db, requested_org, requested_batch, *, lock=False):
                actor = request_actor.get()
                if requested_batch == batch_id and actor == "second":
                    pids["second"] = await db.scalar(text("SELECT pg_backend_pid()"))
                    second_entered.set()
                row = await original_load(db, requested_org, requested_batch, lock=lock)
                if requested_batch == batch_id and actor == "first":
                    pids["first"] = await db.scalar(text("SELECT pg_backend_pid()"))
                    first_loaded.set()
                    await release_first.wait()
                if requested_batch == batch_id and actor == "second":
                    second_loaded.set()
                    await release_second.wait()
                return row

            monkeypatch.setattr(routes, "_load", scheduled_load)

            async def request(operation, actor=None):
                token = request_actor.set(actor)
                try:
                    if operation == "cancel":
                        return await client.delete(f"/api/v1/reimbursements/{batch_id}")
                    if operation == "pay":
                        return await client.post(
                            f"/api/v1/reimbursements/{batch_id}/pay",
                            json={"version": 1, "reference": "EX19-SYNTHETIC-PAYMENT"},
                        )
                    suffix = "export" if operation == "csv" else "sepa"
                    return await client.post(f"/api/v1/reimbursements/{batch_id}/{suffix}")
                finally:
                    request_actor.reset(token)

            tasks.append(asyncio.create_task(request(first, "first"), name="ex19-first"))
            await asyncio.wait_for(first_loaded.wait(), timeout=10)
            tasks.append(asyncio.create_task(request(second, "second"), name="ex19-second"))
            await asyncio.wait_for(second_entered.wait(), timeout=10)
            assert pids["first"] != pids["second"]

            async def observe_database_wait():
                async with sm() as observer:
                    while not await observer.scalar(
                        text(
                            "SELECT CAST(:holder AS integer) = "
                            "ANY(pg_blocking_pids(CAST(:waiter AS integer)))"
                        ),
                        {"holder": pids["first"], "waiter": pids["second"]},
                    ):
                        assert not tasks[1].done(), "second route did not wait for the batch lock"
                        await asyncio.sleep(0.01)

            await asyncio.wait_for(observe_database_wait(), timeout=10)
            release_first.set()
            winner = await asyncio.wait_for(tasks[0], timeout=15)
            assert winner.status_code == (204 if first == "cancel" else 200), winner.text
            await asyncio.wait_for(second_loaded.wait(), timeout=10)
            committed_winner = await _route_state(sm, org_id, batch_id, report_id)
            release_second.set()
            loser = await asyncio.wait_for(tasks[1], timeout=15)
            assert loser.status_code == 409, loser.text
            if first in ("csv", "sepa"):
                assert loser.json()["code"] == "batch_exported_cannot_cancel"
            elif second in ("csv", "sepa"):
                assert loser.json()["code"] == "batch_not_exportable"

            state = await _route_state(sm, org_id, batch_id, report_id)
            assert state == committed_winner
            final_batch, final_report, actions, deliveries, jobs = state
            assert final_batch["total_eur"] == Decimal("60.00")
            assert final_report["total_eur"] == Decimal("60.00")
            if first in ("csv", "sepa"):
                assert final_batch["status"] == BATCH_OPEN
                assert final_batch["version"] == 1
                assert final_batch["export_count"] == 1
                assert final_batch["exported_at"] is not None
                assert bool(final_batch["last_msg_id"]) == (first == "sepa")
                assert final_report["payout_batch_id"] == batch_id
                assert final_report["status"] == "marked_for_reimbursement"
                assert final_report["payment_reference"] is None
                assert actions == [audit.A.REIMBURSE_EXPORTED]
                assert deliveries == jobs == set()
            elif first == "pay":
                assert final_batch["status"] == BATCH_PAID
                assert final_batch["version"] == 2
                assert final_batch["paid_at"] is not None
                assert final_batch["reference"] == "EX19-SYNTHETIC-PAYMENT"
                assert final_report["payout_batch_id"] == batch_id
                assert final_report["status"] == "reimbursed"
                assert final_report["payment_reference"] == "EX19-SYNTHETIC-PAYMENT"
                assert actions == [audit.A.REIMBURSE_PAID]
                assert len(deliveries) == len(jobs) == 1
            else:
                assert final_batch["status"] == "cancelled"
                assert final_batch["version"] == 2
                assert final_batch["export_count"] == 0
                assert final_batch["exported_at"] is None
                assert final_batch["last_msg_id"] is None
                assert final_batch["paid_at"] is None
                assert final_report["payout_batch_id"] is None
                assert final_report["status"] == "marked_for_reimbursement"
                assert final_report["payment_reference"] is None
                assert actions == [audit.A.REIMBURSE_CANCEL]
                assert deliveries == jobs == set()

            # A later refused retry must change no batch/report columns and add
            # no cancellation/payment/export audit or persisted event intent.
            retry = await request(second)
            assert retry.status_code == 409, retry.text
            assert await _route_state(sm, org_id, batch_id, report_id) == state
    finally:
        release_first.set()
        release_second.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        app.dependency_overrides.clear()
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
