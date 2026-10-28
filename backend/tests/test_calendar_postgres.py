"""Calendar OAuth claims, disconnect fencing and raw RLS on non-superuser PostgreSQL."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_session
from app.core.security import decode_access_token
from app.core.tenant import reset_current_org, set_current_org
from app.main import app
from app.models.base import Base
from app.models.calendar_connection import (
    CalendarConnection,
    CalendarEventMapping,
    CalendarOAuthAttempt,
)
from app.models.job import Job
from app.models.organization import Organization
from app.services import calendar_providers, calendar_sync, jobs

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")


@pytest_asyncio.fixture
async def calendar_pg():
    engine = create_async_engine(PG_URL, pool_size=6, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        privileged = await db.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        )
        assert privileged is False, "Calendar security proof must run under enforced RLS"

    async def session():
        async with factory() as db:
            yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = session
    organizations = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield factory, client, organizations
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        async with factory() as db:
            # Registration creates audit/session rows with restrictive FKs. Remove
            # only this fixture's tenants, children first, without resetting a
            # shared PG database or leaving unique synthetic credentials behind.
            for table in reversed(Base.metadata.sorted_tables):
                if "org_id" in table.c:
                    await db.execute(table.delete().where(table.c.org_id.in_(organizations)))
            await db.execute(delete(Organization).where(Organization.id.in_(organizations)))
            await db.commit()
        await engine.dispose()


async def _register(client, organizations):
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Overlapping calendar PG workspace",
            "name": "Synthetic Owner",
            "email": f"calendar-pg-{uuid.uuid4().hex}@example.com",
            "password": "Synthetic-Calendar-Postgres-Password-32",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    organizations.append(body["organization"]["id"])
    body["headers"] = {"Authorization": "Bearer " + body["token"]["access_token"]}
    return body


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_client_id", "synthetic-client")
    monkeypatch.setattr(settings, "google_calendar_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "api_public_base_url", "https://api.calendar.example")
    monkeypatch.setattr(settings, "calendar_public_app_url", "https://calendar.example")


@pytest.mark.asyncio
async def test_calendar_tables_raw_rls_and_actual_connection_summary_pg(calendar_pg):
    factory, client, organizations = calendar_pg
    identities = [await _register(client, organizations) for _ in range(2)]
    evidence = []
    async with factory() as db:
        for index, identity in enumerate(identities):
            org_id, user_id = identity["organization"]["id"], identity["user"]["id"]
            claims = decode_access_token(identity["token"]["access_token"])
            assert claims is not None
            connection = CalendarConnection(
                org_id=org_id,
                user_id=user_id,
                provider="google",
                status="connected",
                generation=1,
                calendar_name="Overlapping private calendar",
                calendar_id="overlapping-calendar-id",
                sealed_tokens="SYNTHETIC-CREDENTIAL-NEVER-SERIALIZED",
            )
            db.add(connection)
            await db.flush()
            attempt = CalendarOAuthAttempt(
                org_id=org_id,
                user_id=user_id,
                session_id=claims["jti"],
                provider="google",
                connection_id=connection.id,
                connection_generation=1,
                state_hash=uuid.uuid4().hex * 2,
                browser_hash=uuid.uuid4().hex * 2,
                sealed_verifier="SYNTHETIC-PRIVATE-PKCE",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
            db.add(attempt)
            mapping_ids = set()
            for _ in range(index + 1):
                mapping = CalendarEventMapping(
                    org_id=org_id,
                    connection_id=connection.id,
                    assignment_id=str(uuid.uuid4()),
                    stable_id=str(uuid.uuid4()),
                    status="queued",
                )
                db.add(mapping)
                await db.flush()
                mapping_ids.add(mapping.id)
            await db.flush()
            evidence.append(
                {
                    "calendar_connections": {connection.id},
                    "calendar_oauth_attempts": {attempt.id},
                    "calendar_event_mappings": mapping_ids,
                }
            )
        await db.commit()

    for index, identity in enumerate(identities):
        org_id = identity["organization"]["id"]
        async with factory() as db:
            await db.execute(
                text("SELECT set_config('app.current_org', :org, true)"), {"org": org_id}
            )
            # Raw SQL bypasses ORM filtering: PostgreSQL itself must isolate all three tables.
            for table in evidence[index]:
                seen = set(
                    map(
                        str,
                        (
                            await db.execute(
                                text(f"SELECT id FROM {table} WHERE org_id IN (:a, :b)"),
                                {"a": organizations[0], "b": organizations[1]},
                            )
                        ).scalars(),
                    )
                )
                assert seen == evidence[index][table], (table, index, seen)
                changed = (
                    (
                        await db.execute(
                            text(
                                f"UPDATE {table} SET updated_at=now() WHERE org_id=:other RETURNING id"
                            ),
                            {"other": organizations[1 - index]},
                        )
                    )
                    .scalars()
                    .all()
                )
                assert changed == [], (table, "cross-tenant raw update succeeded")
            await db.rollback()

        # WITH CHECK must reject moving an otherwise visible row into the other tenant.
        for table in evidence[index]:
            async with factory() as db:
                await db.execute(
                    text("SELECT set_config('app.current_org', :org, true)"), {"org": org_id}
                )
                with pytest.raises(DBAPIError) as refused:
                    await db.execute(
                        text(f"UPDATE {table} SET org_id=:other WHERE id=:id"),
                        {
                            "other": organizations[1 - index],
                            "id": next(iter(evidence[index][table])),
                        },
                    )
                assert getattr(refused.value.orig, "sqlstate", None) == "42501"
                await db.rollback()

        summary = await client.get(
            "/api/v1/schedule/calendar-connections", headers=identity["headers"]
        )
        assert summary.status_code == 200, summary.text
        google = next(p for p in summary.json()["providers"] if p["provider"] == "google")
        assert google["connection"]["id"] in evidence[index]["calendar_connections"]
        assert google["connection"]["pending"] == index + 1
        assert google["connection"]["failed"] == 0
        assert "SYNTHETIC-CREDENTIAL" not in summary.text
        assert "overlapping-calendar-id" not in summary.text


@pytest.mark.asyncio
async def test_callback_is_claimed_once_and_disconnect_fences_inflight_exchange_pg(
    calendar_pg, monkeypatch
):
    factory, client, organizations = calendar_pg
    _configure(monkeypatch)
    identity = await _register(client, organizations)
    exchanges = []
    exchange_started, release_exchange = asyncio.Event(), asyncio.Event()

    async def exchange(_provider, code, verifier, _redirect_uri):
        assert len(verifier) >= 43
        exchanges.append(code)
        if code == "disconnect-during-exchange":
            exchange_started.set()
            await asyncio.wait_for(release_exchange.wait(), timeout=15)
        return {
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "expires_in": 3600,
        }

    async def account_key(_provider, _token):
        return "synthetic-provider-account"

    async def find_calendar(_provider, _token, _name, *, marker):
        # Exercise the service/client marker contract before accepting a remote proof.
        assert str(uuid.UUID(marker)) == marker
        return None

    async def create_calendar(_provider, _token, *, name, marker):
        assert name and str(uuid.UUID(marker)) == marker
        return "synthetic-managed-calendar"

    monkeypatch.setattr(calendar_providers, "exchange_code", exchange)
    monkeypatch.setattr(calendar_providers, "account_key", account_key)
    monkeypatch.setattr(calendar_providers, "find_calendar", find_calendar)
    monkeypatch.setattr(calendar_providers, "create_calendar", create_calendar)

    async def start():
        result = await client.post(
            "/api/v1/schedule/calendar-connections/google/connect", headers=identity["headers"]
        )
        assert result.status_code == 200, result.text
        state = parse_qs(urlparse(result.json()["authorization_url"]).query)["state"][0]
        cookies = SimpleCookie()
        cookies.load(result.headers["set-cookie"])
        return state, cookies[calendar_sync.OAUTH_COOKIE].value

    async def callback(state, binding, code):
        return await client.get(
            "/api/v1/schedule/calendar-connections/google/callback",
            params={"state": state, "code": code},
            headers={"Cookie": f"{calendar_sync.OAUTH_COOKIE}={binding}"},
            follow_redirects=False,
        )

    state, binding = await start()
    results = await asyncio.gather(*[callback(state, binding, "same-command") for _ in range(2)])
    assert sorted(r.headers["location"].rsplit("=", 1)[-1] for r in results) == [
        "connected",
        "failed",
    ]
    assert exchanges == ["same-command"]
    async with factory() as db:
        connected = await db.scalar(
            select(CalendarConnection).where(CalendarConnection.org_id == organizations[0])
        )
        original_generation = connected.generation
        assert connected.status == "connected" and connected.sealed_tokens.startswith("kv1.")

    state, binding = await start()
    pending = asyncio.create_task(callback(state, binding, "disconnect-during-exchange"))
    try:
        await asyncio.wait_for(exchange_started.wait(), timeout=10)
        removed = await client.delete(
            "/api/v1/schedule/calendar-connections/google", headers=identity["headers"]
        )
        assert removed.status_code == 204, removed.text
    finally:
        release_exchange.set()
        result = await asyncio.wait_for(pending, timeout=15)
    assert result.headers["location"].endswith("calendar_sync=failed")
    async with factory() as db:
        disconnected = await db.scalar(
            select(CalendarConnection).where(CalendarConnection.org_id == organizations[0])
        )
        assert disconnected.generation > original_generation
        assert disconnected.status == "disconnected" and disconnected.sealed_tokens is None
        assert (
            await db.scalars(
                select(CalendarOAuthAttempt).where(CalendarOAuthAttempt.org_id == organizations[0])
            )
        ).all() == []


@pytest.mark.asyncio
async def test_concurrent_native_workers_export_one_event_and_commit_intent_before_io_pg(
    calendar_pg, monkeypatch
):
    factory, client, organizations = calendar_pg
    _configure(monkeypatch)
    identity = await _register(client, organizations)
    org_id, user_id = identity["organization"]["id"], identity["user"]["id"]
    async with factory() as db:
        connection = CalendarConnection(
            org_id=org_id,
            user_id=user_id,
            provider="google",
            status="connected",
            generation=1,
            calendar_id="synthetic-private-calendar",
            calendar_name="Synthetic private work schedule",
            calendar_marker=str(uuid.uuid4()),
        )
        db.add(connection)
        await db.flush()
        calendar_sync._seal_tokens(
            connection,
            {
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "expires_in": 3600,
            },
        )
        connection_id = connection.id
        await db.commit()
    project = await client.post(
        "/api/v1/masters/projects",
        json={"code": "CAL-PG", "name": "Synthetic on-site work"},
        headers=identity["headers"],
    )
    assert project.status_code == 201, project.text
    starts = datetime.now(UTC) + timedelta(days=2)
    assignment = await client.post(
        "/api/v1/schedule/assignments",
        json={
            "project_id": project.json()["id"],
            "assignee_user_id": user_id,
            "starts_at": starts.isoformat(),
            "ends_at": (starts + timedelta(hours=2)).isoformat(),
            "notify_assignee": False,
            "reminders_enabled": False,
            "client_notice_enabled": False,
            "note": "Synthetic site access instruction",
        },
        headers=identity["headers"],
    )
    assert assignment.status_code == 201, assignment.text
    source_id = assignment.json()["assignment"]["id"]
    async with factory() as db:
        original_job = await db.scalar(
            select(Job).where(Job.org_id == org_id, Job.kind == calendar_sync.SYNC_JOB)
        )
        assert original_job is not None, "The planning API must commit its calendar outbox job"
        # Simulate at-least-once delivery using two persisted queue messages for
        # one mapping revision. They must converge on one remote object.
        await jobs.enqueue(
            db,
            calendar_sync.SYNC_JOB,
            json.loads(original_job.payload_json),
            org_id=org_id,
            idempotency_key=f"synthetic-duplicate-{uuid.uuid4().hex}",
        )

    reached_provider, release_provider = asyncio.Event(), asyncio.Event()
    creates = []

    async def find_event(*_args):
        return None

    async def put_event(
        provider, token, calendar_id, event_id, stable_id, payload, *, allow_create
    ):
        assert provider == "google" and token == "synthetic-access"
        assert calendar_id == "synthetic-private-calendar" and event_id is None and allow_create
        assert payload["description"] == "Synthetic site access instruction"
        creates.append(stable_id)
        reached_provider.set()
        await asyncio.wait_for(release_provider.wait(), timeout=15)
        return "synthetic-one-remote-event"

    monkeypatch.setattr(calendar_providers, "find_event", find_event)
    monkeypatch.setattr(calendar_providers, "put_event", put_event)
    barrier = asyncio.Barrier(2)

    async def worker(index, *, ready_at=None):
        tenant_token = set_current_org(org_id)
        try:
            async with factory() as db:
                if ready_at is None:
                    await asyncio.wait_for(barrier.wait(), timeout=10)
                return await jobs.run_once(
                    db,
                    f"synthetic-calendar-worker-{index}",
                    kinds=(calendar_sync.SYNC_JOB,),
                    now=ready_at,
                )
        finally:
            reset_current_org(tenant_token)

    workers = [asyncio.create_task(worker(index)) for index in range(2)]
    try:
        await asyncio.wait_for(reached_provider.wait(), timeout=10)
        async with factory() as observer:
            committed = await observer.scalar(
                select(CalendarEventMapping).where(
                    CalendarEventMapping.org_id == org_id,
                    CalendarEventMapping.connection_id == connection_id,
                    CalendarEventMapping.assignment_id == source_id,
                )
            )
            assert committed.status == "syncing" and committed.create_started is True
            assert committed.lease_owner is not None and committed.event_id is None
    finally:
        release_provider.set()
        outcomes = await asyncio.wait_for(asyncio.gather(*workers), timeout=20)
    assert all(
        job is not None and job.org_id == org_id and job.status != "dead" for job in outcomes
    )
    assert len(creates) == 1
    # A loser may have deferred while the winner held the mapping lease. Drain
    # that persisted retry; it must observe the acknowledged revision and stop.
    for _ in range(3):
        if await worker("recovery", ready_at=datetime.now(UTC) + timedelta(minutes=10)) is None:
            break
    async with factory() as db:
        mapping = await db.scalar(
            select(CalendarEventMapping).where(
                CalendarEventMapping.org_id == org_id,
                CalendarEventMapping.connection_id == connection_id,
                CalendarEventMapping.assignment_id == source_id,
            )
        )
        assert mapping.status == "synced" and mapping.event_id == "synthetic-one-remote-event"
        assert mapping.synced_revision == mapping.desired_revision == 1
        assert mapping.lease_owner is None
    assert len(creates) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_at", ["before_setup_lock", "after_intent_commit"])
async def test_setup_refreshes_cached_connection_before_provider_io_pg(
    calendar_pg, monkeypatch, disconnect_at
):
    factory, client, organizations = calendar_pg
    _configure(monkeypatch)
    identity = await _register(client, organizations)
    org_id, user_id = identity["organization"]["id"], identity["user"]["id"]
    provider_calls = []

    async def must_not_find(*args, **kwargs):
        provider_calls.append((args, kwargs))
        pytest.fail("A committed disconnect must fence setup even with a cached connection")

    monkeypatch.setattr(calendar_providers, "find_calendar", must_not_find)
    async with factory() as db:
        cached = CalendarConnection(
            org_id=org_id,
            user_id=user_id,
            provider="google",
            status="connecting",
            generation=1,
            calendar_marker=str(uuid.uuid4()),
        )
        db.add(cached)
        await db.flush()
        calendar_sync._seal_tokens(
            cached,
            {
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "expires_in": 3600,
            },
        )
        await db.commit()
        connection_id, generation = cached.id, cached.generation

        async def disconnect():
            response = await client.delete(
                "/api/v1/schedule/calendar-connections/google", headers=identity["headers"]
            )
            assert response.status_code == 204, response.text

        if disconnect_at == "before_setup_lock":
            await disconnect()
        else:
            original_commit = db.commit
            disconnected = False

            async def commit_then_disconnect():
                nonlocal disconnected
                await original_commit()
                if not disconnected:
                    disconnected = True
                    await disconnect()

            monkeypatch.setattr(db, "commit", commit_then_disconnect)
        outcome = await calendar_sync._finish_setup(db, connection_id, org_id, generation)
        assert outcome == {"synced": False, "reason": "disconnected"}
        assert not provider_calls
    async with factory() as observer:
        ended = await observer.get(CalendarConnection, connection_id)
        assert ended.status == "disconnected" and ended.sealed_tokens is None
        assert ended.generation > generation


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["setup", "reconcile"])
async def test_parallel_retry_requests_coalesce_only_the_active_connection_generation_pg(
    calendar_pg, monkeypatch, purpose
):
    factory, client, organizations = calendar_pg
    _configure(monkeypatch)
    identity = await _register(client, organizations)
    org_id, user_id = identity["organization"]["id"], identity["user"]["id"]
    async with factory() as db:
        connection = CalendarConnection(
            org_id=org_id,
            user_id=user_id,
            provider="google",
            generation=3,
            status="setup_unknown" if purpose == "setup" else "connected",
            calendar_id=None if purpose == "setup" else "synthetic-managed-calendar",
            calendar_marker=str(uuid.uuid4()),
        )
        db.add(connection)
        await db.flush()
        connection_id = connection.id
        # Old-generation work and completed/failed current-generation work must
        # not suppress a fresh recovery command. Only one live current chain
        # may remain after the two HTTP requests have both committed.
        for generation, status in (
            (2, "queued"),
            (2, "running"),
            (3, "failed"),
            (3, "dead"),
            (3, "succeeded"),
        ):
            db.add(
                Job(
                    org_id=org_id,
                    kind=calendar_sync.SYNC_JOB,
                    payload_json=json.dumps(
                        {"connection_id": connection_id, "generation": generation, purpose: True}
                    ),
                    idempotency_key=f"calendar.{purpose}:{connection_id}:{generation}:{uuid.uuid4().hex}",
                    status=status,
                )
            )
        await db.commit()

    responses = await asyncio.gather(
        *[
            client.post(
                "/api/v1/schedule/calendar-connections/google/retry",
                headers=identity["headers"],
            )
            for _ in range(2)
        ]
    )
    assert [response.status_code for response in responses] == [202, 202], [
        response.text for response in responses
    ]
    assert all(response.json() == {"status": "queued"} for response in responses)
    async with factory() as observer:
        queued = (
            await observer.scalars(
                select(Job).where(Job.org_id == org_id, Job.kind == calendar_sync.SYNC_JOB)
            )
        ).all()
        active_current = []
        for job in queued:
            payload = json.loads(job.payload_json)
            if (
                payload.get("connection_id") == connection_id
                and payload.get("generation") == 3
                and payload.get(purpose) is True
                and job.status in {"queued", "running"}
            ):
                active_current.append(job)
        assert len(active_current) == 1
        assert len(queued) == 6, "Both retries must reuse one new chain, preserving prior evidence"
