"""Session tenant projection through real PostgreSQL RLS, not SQLite stand-ins."""

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from tests.test_session_org_isolation import assert_independent_sessions, headers, pair

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated PostgreSQL")


@pytest.mark.asyncio
async def test_session_projection_and_serialized_switch_precondition_pg():
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = get_test_session
    try:
        async with factory() as db:
            privileged = await db.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
            assert privileged is False
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await assert_independent_sessions(client, db)
                one, two, alpha, beta, uid = await pair(client, db)
                # Both callers reviewed the same source workspace. Only the
                # first switch can consume that precondition under the row lock.
                responses = await asyncio.gather(
                    *[
                        client.post(f"/api/v1/auth/switch-org/{beta}", headers=headers(two, alpha))
                        for _ in range(2)
                    ]
                )
                assert sorted(r.status_code for r in responses) == [200, 409], [
                    r.text for r in responses
                ]
                assert (await client.get("/api/v1/auth/me", headers=headers(one))).json()[
                    "organization"
                ]["id"] == alpha
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_member_role_change_locks_before_audit_during_expense_command(
    monkeypatch, record_property
):
    """Real routes agree on membership -> audit ordering, without a lock cycle.

    The financial request holds its original actor lock. A concurrent member
    change must wait there BEFORE acquiring the tenant audit lock. The old plain
    team read acquired audit first and then blocked flushing that membership;
    releasing the financial command produced an actual PostgreSQL wait cycle.
    The wrappers below schedule original loaders/audits and never add locks.
    """
    from contextvars import ContextVar
    from decimal import Decimal
    from time import perf_counter
    from uuid import uuid4

    from sqlalchemy import select

    from app.core.tenant import reset_current_org, set_current_org
    from app.models.audit import AuditEvent
    from app.models.expense import ExpenseCommand, ExpenseReport, ReimbursementBatch
    from app.models.job import Job
    from app.models.membership import Membership
    from app.models.session import Session
    from app.models.user import User
    from app.models.webhook import WebhookDelivery, WebhookEndpoint
    from app.services import audit, expense_commands, team

    engine = create_async_engine(PG_URL, pool_size=6, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = get_test_session
    request_kind = ContextVar("membership_audit_request_kind", default=None)
    command_locked = asyncio.Event()
    release_command = asyncio.Event()
    team_entered_loader = asyncio.Event()
    team_entered_audit = asyncio.Event()
    command_entered_audit = asyncio.Event()
    original_actor = expense_commands.lock_actor
    original_member = team.get_member
    original_audit = audit.record
    pids = {}
    tasks = []
    started = perf_counter()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            stamp = uuid4().hex[:12]
            registered = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": f"Synthetic member-audit schedule {stamp}",
                    "name": "Synthetic owner",
                    "email": f"member-audit-owner-{stamp}@example.com",
                    "password": "supersecret",
                },
            )
            assert registered.status_code == 201
            owner = registered.json()
            org_id = owner["user"]["org_id"]
            owner_headers = {"Authorization": f"Bearer {owner['token']['access_token']}"}
            enabled = await client.put(
                "/api/v1/modules/expenses", json={"enabled": True}, headers=owner_headers
            )
            assert enabled.status_code == 200
            invitation = await client.post(
                "/api/v1/team/invites",
                json={"email": f"member-audit-admin-{stamp}@example.com", "role": "admin"},
                headers=owner_headers,
            )
            assert invitation.status_code == 201
            accepted = await client.post(
                "/api/v1/auth/accept-invite",
                json={
                    "token": invitation.json()["token"],
                    "name": "Synthetic command approver",
                    "password": "supersecret",
                },
            )
            assert accepted.status_code == 201
            approver = accepted.json()
            approver_id = approver["user"]["id"]
            approver_headers = {"Authorization": f"Bearer {approver['token']['access_token']}"}
            tenant = set_current_org(org_id)
            try:
                async with factory() as db:
                    report = ExpenseReport(
                        org_id=org_id,
                        employee_id=owner["user"]["id"],
                        employee_name="Synthetic distinct claimant",
                        title="Synthetic approved parking",
                        status="approved",
                        currency="EUR",
                        total=Decimal("12.34"),
                        total_eur=Decimal("12.34"),
                        vat_total=Decimal("0.00"),
                        version=7,
                    )
                    db.add(report)
                    db.add(
                        WebhookEndpoint(
                            org_id=org_id,
                            url="https://example.test/member-audit-synthetic",
                            secret="synthetic-unused-secret",
                            events="expense.reimbursement_created",
                        )
                    )
                    await db.flush()
                    report_id = report.id
                    await db.commit()
            finally:
                reset_current_org(tenant)

            async def scheduled_actor(db, current, **kwargs):
                await original_actor(db, current, **kwargs)
                if request_kind.get() == "command":
                    pids["command"] = await db.scalar(text("SELECT pg_backend_pid()"))
                    command_locked.set()
                    await release_command.wait()

            async def scheduled_member(db, requested_org, user_id, **kwargs):
                if request_kind.get() == "team":
                    pids["team"] = await db.scalar(text("SELECT pg_backend_pid()"))
                    team_entered_loader.set()
                return await original_member(db, requested_org, user_id, **kwargs)

            async def scheduled_audit(db, action, **kwargs):
                if request_kind.get() == "team":
                    team_entered_audit.set()
                elif request_kind.get() == "command":
                    command_entered_audit.set()
                return await original_audit(db, action, **kwargs)

            monkeypatch.setattr(expense_commands, "lock_actor", scheduled_actor)
            monkeypatch.setattr(team, "get_member", scheduled_member)
            monkeypatch.setattr(audit, "record", scheduled_audit)

            async def command_request():
                token = request_kind.set("command")
                try:
                    return await client.post(
                        "/api/v1/reimbursements",
                        headers={**approver_headers, "Idempotency-Key": str(uuid4())},
                        json={
                            "report_ids": [report_id],
                            "report_versions": {report_id: 7},
                        },
                    )
                finally:
                    request_kind.reset(token)

            async def team_request():
                token = request_kind.set("team")
                try:
                    return await client.patch(
                        f"/api/v1/team/members/{approver_id}",
                        headers=owner_headers,
                        json={"role": "user"},
                    )
                finally:
                    request_kind.reset(token)

            tasks.append(asyncio.create_task(command_request(), name="member-audit-command"))
            await asyncio.wait_for(command_locked.wait(), timeout=10)
            tasks.append(asyncio.create_task(team_request(), name="member-audit-role-change"))
            await asyncio.wait_for(team_entered_loader.wait(), timeout=10)
            assert pids["command"] != pids["team"]
            wait_checks = 0

            async def membership_wait():
                nonlocal wait_checks
                async with factory() as observer:
                    while True:
                        wait_checks += 1
                        blocked = await observer.scalar(
                            text(
                                "SELECT CAST(:holder AS integer) = ANY(pg_blocking_pids(CAST(:waiter AS integer)))"
                            ),
                            {"holder": pids["command"], "waiter": pids["team"]},
                        )
                        if blocked:
                            return bool(
                                await observer.scalar(
                                    text(
                                        "SELECT count(*) > 0 FROM pg_locks WHERE pid = :pid AND locktype = 'advisory' AND granted"
                                    ),
                                    {"pid": pids["team"]},
                                )
                            )
                        assert not tasks[1].done(), "member update did not wait for actor authority"
                        await asyncio.sleep(0.01)

            team_had_audit_lock = await asyncio.wait_for(membership_wait(), timeout=10)
            audit_before_membership = team_entered_audit.is_set()
            release_command.set()
            lock_cycle = False

            async def observe_completion():
                nonlocal lock_cycle
                async with factory() as observer:
                    while not all(task.done() for task in tasks):
                        command_waits_team = await observer.scalar(
                            text(
                                "SELECT CAST(:holder AS integer) = ANY(pg_blocking_pids(CAST(:waiter AS integer)))"
                            ),
                            {"holder": pids["team"], "waiter": pids["command"]},
                        )
                        team_waits_command = await observer.scalar(
                            text(
                                "SELECT CAST(:holder AS integer) = ANY(pg_blocking_pids(CAST(:waiter AS integer)))"
                            ),
                            {"holder": pids["command"], "waiter": pids["team"]},
                        )
                        if command_waits_team and team_waits_command:
                            lock_cycle = True
                            return
                        await asyncio.sleep(0.01)

            await asyncio.wait_for(observe_completion(), timeout=15)
            command, changed = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
            record_property("distinct_database_pids", True)
            record_property("team_wait_on_actor_membership_observed", True)
            record_property(
                "team_audit_started_before_membership_available", audit_before_membership
            )
            record_property("team_advisory_lock_held_while_waiting_membership", team_had_audit_lock)
            record_property("mutual_database_blocking_observed", lock_cycle)
            record_property("command_status", command.status_code)
            record_property("member_change_status", changed.status_code)
            record_property("observer_wait_queries", wait_checks)
            record_property("elapsed_seconds", round(perf_counter() - started, 6))
            assert not lock_cycle, (
                "original routes formed membership/audit mutual PostgreSQL blocking"
            )
            assert not audit_before_membership
            assert not team_had_audit_lock
            assert command.status_code == 201, command.text
            assert changed.status_code == 200, changed.text
            assert changed.json()["role"] == "user"
            assert (
                await client.get("/api/v1/auth/me", headers=approver_headers)
            ).status_code == 401

            tenant = set_current_org(org_id)
            try:
                async with factory() as db:
                    member = await db.scalar(
                        select(Membership).where(
                            Membership.org_id == org_id, Membership.user_id == approver_id
                        )
                    )
                    assert member.role.value == "user" and member.status == "active"
                    profile = await db.get(User, approver_id)
                    assert profile.role.value == "user" and profile.is_active
                    sessions = list(
                        await db.scalars(
                            select(Session).where(
                                Session.org_id == org_id, Session.user_id == approver_id
                            )
                        )
                    )
                    assert sessions and all(row.revoked_at is not None for row in sessions)
                    batches = list(
                        await db.scalars(
                            select(ReimbursementBatch).where(ReimbursementBatch.org_id == org_id)
                        )
                    )
                    receipts = list(
                        await db.scalars(
                            select(ExpenseCommand).where(ExpenseCommand.org_id == org_id)
                        )
                    )
                    delivery = list(
                        await db.scalars(
                            select(WebhookDelivery).where(WebhookDelivery.org_id == org_id)
                        )
                    )
                    jobs = list(await db.scalars(select(Job).where(Job.org_id == org_id)))
                    reports = list(
                        await db.scalars(
                            select(ExpenseReport).where(ExpenseReport.org_id == org_id)
                        )
                    )
                    assert (
                        len(batches)
                        == len(receipts)
                        == len(delivery)
                        == len(jobs)
                        == len(reports)
                        == 1
                    )
                    assert batches[0].status == "open" and batches[0].total_eur == Decimal("12.34")
                    assert reports[0].payout_batch_id == batches[0].id and reports[0].version == 8
                    assert receipts[0].target_id == batches[0].id
                    assert delivery[0].status == "pending" and delivery[0].attempts == 0
                    assert jobs[0].status == "queued" and jobs[0].attempts == 0
                    events = list(
                        await db.scalars(
                            select(AuditEvent)
                            .where(AuditEvent.org_id == org_id)
                            .order_by(AuditEvent.seq)
                        )
                    )
                    created = [row for row in events if row.action == audit.A.REIMBURSE_BATCH]
                    roles = [
                        row
                        for row in events
                        if row.action == audit.A.ROLE_CHANGE and row.target_id == approver_id
                    ]
                    revoked = [
                        row
                        for row in events
                        if row.action == audit.A.SESSION_REVOKE_BULK
                        and row.target_id == approver_id
                    ]
                    assert len(created) == len(roles) == len(revoked) == 1
                    assert created[0].seq < roles[0].seq < revoked[0].seq
                    assert (await audit.verify_chain(db, org_id)).ok
            finally:
                reset_current_org(tenant)
    finally:
        release_command.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        app.dependency_overrides.clear()
        await engine.dispose()
