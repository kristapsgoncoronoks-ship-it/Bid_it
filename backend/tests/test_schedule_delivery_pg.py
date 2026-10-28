"""Work planning races on independent, non-superuser PostgreSQL connections."""

import asyncio
import json
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import tenant
from app.core.config import settings
from app.core.database import get_session
from app.main import app
from app.models.audit import AuditEvent
from app.models.email_message import EmailMessage
from app.models.job import Job
from app.models.project_assignment import ProjectAssignment
from app.services import mailer, schedule_email, scheduling

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")


@pytest_asyncio.fixture
async def pg_schedule(monkeypatch):
    engine = create_async_engine(PG_URL, pool_size=5, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with sessions() as db:
            yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = get_test_session
    # Bootstrap verification uses the existing generic mailer; keep it offline.
    # The stricter no-I/O trap applies once the scheduling subject is created.
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(
        mailer, "_smtp_send", lambda *args: pytest.fail("SMTP ran before the worker claim")
    )
    try:
        async with sessions() as db:
            assert (
                await db.scalar(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
                is False
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic schedule concurrency",
                    "name": "Synthetic planner",
                    "email": f"schedule-{uuid.uuid4().hex}@example.com",
                    "password": "Synthetic-Schedule-Postgres-Password-32",
                },
            )
            assert registration.status_code == 201, registration.text
            monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
            org_id = registration.json()["organization"]["id"]
            client.headers["Authorization"] = (
                "Bearer " + registration.json()["token"]["access_token"]
            )
            project = await client.post(
                "/api/v1/masters/projects", json={"code": "PG-PLAN", "name": "Synthetic work"}
            )
            assert project.status_code == 201, project.text
            start = datetime.now(UTC) + timedelta(days=7)
            created = await client.post(
                "/api/v1/schedule/assignments",
                json={
                    "project_id": project.json()["id"],
                    "assignee_user_id": registration.json()["user"]["id"],
                    "starts_at": start.isoformat(),
                    "ends_at": (start + timedelta(hours=2)).isoformat(),
                    "time_zone": "Europe/Riga",
                    "reminders_enabled": False,
                    "client_notice_enabled": False,
                },
            )
            assert created.status_code == 201, created.text
            yield client, sessions, org_id, created.json()["assignment"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_edit_has_one_revision_and_one_set_of_secondary_effects(
    pg_schedule, monkeypatch
):
    client, sessions, org_id, row = pg_schedule
    original = scheduling.get
    barrier = asyncio.Barrier(2)
    armed = True

    async def synchronized_get(db, org, assignment_id, *, for_update=False):
        if armed and for_update and assignment_id == row["id"]:
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return await original(db, org, assignment_id, for_update=for_update)

    monkeypatch.setattr(scheduling, "get", synchronized_get)
    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
    try:
        responses = await asyncio.gather(
            *[
                client.patch(
                    f"/api/v1/schedule/assignments/{row['id']}",
                    json={"expected_revision": 1, "note": note},
                )
                for note in ("First reviewed plan", "Second reviewed plan")
            ]
        )
    finally:
        armed = False
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    winner = next(r.json()["assignment"] for r in responses if r.status_code == 200)
    assert winner["revision"] == 2
    assert calls == [], "saving work must not perform external SMTP"
    async with sessions() as db:
        stored = await db.get(ProjectAssignment, row["id"])
        assert stored.revision == 2 and stored.note == winner["note"]
        for model, condition, count in (
            (EmailMessage, EmailMessage.kind == "assignment", 2),
            (Job, Job.kind == schedule_email.KIND, 2),
            (AuditEvent, AuditEvent.action == "assignment.update", 1),
        ):
            assert (
                await db.scalar(
                    select(func.count()).select_from(model).where(model.org_id == org_id, condition)
                )
                == count
            )


@pytest.mark.asyncio
async def test_concurrent_delivery_attempts_make_one_smtp_attempt_after_committed_intent(
    pg_schedule, monkeypatch
):
    _, sessions, org_id, row = pg_schedule
    async with sessions() as db:
        job = await db.scalar(
            select(Job).where(Job.org_id == org_id, Job.kind == schedule_email.KIND)
        )
        payload = json.loads(job.payload_json)
        message = await db.get(EmailMessage, payload["message_id"])
        assert message.status == "queued" and message.sent_at is None
    started, release = threading.Event(), threading.Event()
    calls = []

    def transport(*args):
        calls.append(args)
        started.set()
        assert release.wait(10), "test did not release the mocked relay"

    monkeypatch.setattr(mailer, "_smtp_send", transport)

    async def send():
        token = tenant.set_current_org(org_id)
        try:
            async with sessions() as db:
                return await schedule_email.deliver(db, org_id, payload)
        finally:
            tenant.reset_current_org(token)

    first = asyncio.create_task(send())
    try:
        assert await asyncio.to_thread(started.wait, 10)
        # SMTP is paused, so another connection must see the committed claim.
        async with sessions() as db:
            pending = await db.get(EmailMessage, payload["message_id"])
            assert pending.status == "sending" and pending.sent_at is None
        second = await asyncio.wait_for(send(), timeout=10)
        assert second == {"status": "unknown"}
        assert len(calls) == 1
    finally:
        release.set()
        result = await asyncio.wait_for(first, timeout=10)
    assert result == {"status": "sent"}
    assert await send() == {"status": "sent"}
    assert len(calls) == 1
    async with sessions() as db:
        final = await db.get(EmailMessage, payload["message_id"])
        assert final.status == "sent" and final.sent_at is not None
        stored = await db.get(ProjectAssignment, row["id"])
        assert stored.revision == 1


@pytest.mark.asyncio
async def test_parallel_safe_retry_enqueues_one_new_job(pg_schedule, monkeypatch):
    client, sessions, org_id, row = pg_schedule
    async with sessions() as db:
        message = await db.scalar(
            select(EmailMessage).where(
                EmailMessage.org_id == org_id, EmailMessage.kind == "assignment"
            )
        )
        message.status = "failed"
        message.error = "Synthetic definite relay rejection"
        await db.commit()
        message_id = message.id
    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
    responses = await asyncio.gather(
        *[
            client.post(
                f"/api/v1/schedule/assignments/{row['id']}/notifications/{message_id}/retry"
            )
            for _ in range(3)
        ]
    )
    assert [r.status_code for r in responses] == [200, 200, 200], [r.text for r in responses]
    assert {r.json()["status"] for r in responses} == {"queued"}
    assert calls == []
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.org_id == org_id, Job.kind == schedule_email.KIND)
            )
            == 2
        )
