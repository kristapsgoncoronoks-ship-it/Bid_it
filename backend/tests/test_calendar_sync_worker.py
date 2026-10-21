"""Persisted calendar export outcomes, replay recovery and owner revocation.

Provider I/O is synthetic; these contracts exercise the real ORM, planner API,
sealed credentials, and durable export service on the isolated test database.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.core import keyvault
from app.core.config import settings
from app.models.calendar_connection import CalendarConnection, CalendarEventMapping
from app.models.job import Job
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.project_assignment import ProjectAssignment
from app.models.user import User
from app.services import calendar_providers as providers
from app.services import calendar_sync, jobs


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for provider in ("google", "outlook"):
        monkeypatch.setattr(settings, f"{provider}_calendar_client_id", "synthetic-client")
        monkeypatch.setattr(settings, f"{provider}_calendar_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "api_public_base_url", "http://localhost:8000")
    monkeypatch.setattr(settings, "calendar_public_app_url", "http://localhost:5173")
    monkeypatch.setattr(settings, "environment", "development")


@pytest.fixture
def remote(monkeypatch):
    values = SimpleNamespace(
        find_event=AsyncMock(return_value=None),
        put_event=AsyncMock(return_value="synthetic-remote-event"),
        delete_event=AsyncMock(),
        refresh_tokens=AsyncMock(
            return_value={
                "access_token": "fresh-synthetic-access",
                "refresh_token": "fresh-synthetic-refresh",
                "expires_in": 3600,
            }
        ),
    )
    for name, value in vars(values).items():
        monkeypatch.setattr(providers, name, value)
    return values


async def _connection(db, org_id, user_id, provider):
    conn = CalendarConnection(
        id=str(uuid.uuid4()),
        org_id=org_id,
        user_id=user_id,
        provider=provider,
        generation=1,
        status="connected",
        calendar_id=f"synthetic-{user_id}-calendar",
        calendar_name="Acme work schedule",
        calendar_marker=str(uuid.uuid4()),
    )
    db.add(conn)
    await db.flush()
    calendar_sync._seal_tokens(
        conn,
        {
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "expires_in": 3600,
        },
    )
    await db.commit()
    return conn


async def _seed(client, db, provider="google", *, queue=True, **assignment):
    me = (await client.get("/api/v1/auth/me")).json()
    project = await client.post(
        "/api/v1/masters/projects", json={"code": "CAL-WORK", "name": "Replace windows"}
    )
    assert project.status_code in (200, 201), project.text
    created = await client.post(
        "/api/v1/schedule/assignments",
        json={
            "project_id": project.json()["id"],
            "assignee_user_id": me["user"]["id"],
            "starts_at": "2026-10-24T09:00:00+00:00",
            "ends_at": "2026-10-24T17:00:00+00:00",
            "note": "Use the rear entrance",
            "notify_assignee": False,
            "reminders_enabled": False,
            "client_notice_enabled": False,
            **assignment,
        },
    )
    assert created.status_code == 201, created.text
    row = await db.get(ProjectAssignment, created.json()["assignment"]["id"])
    conn = await _connection(db, row.org_id, row.assignee_user_id, provider)
    case = SimpleNamespace(row=row, conn=conn, owner=me["user"], project=project.json())
    if queue:
        await calendar_sync.enqueue_assignment_sync(db, row)
        await db.commit()
        await _queued(db, case)
    return case


async def _queued(db, case, *, connection=None):
    connection = connection or case.conn
    mapping = await db.scalar(
        select(CalendarEventMapping)
        .where(
            CalendarEventMapping.connection_id == connection.id,
            CalendarEventMapping.assignment_id == case.row.id,
        )
        .execution_options(populate_existing=True)
    )
    queued = list(
        (
            await db.scalars(
                select(Job).where(Job.org_id == connection.org_id, Job.kind == "calendar.sync")
            )
        ).all()
    )
    matching = [
        job
        for job in queued
        if json.loads(job.payload_json).get("connection_id") == connection.id
        and json.loads(job.payload_json).get("assignment_id") == case.row.id
    ]
    assert mapping is not None and matching
    # Each planner revision has its own idempotency key. Select by that key,
    # rather than timestamp ordering (SQLite timestamps may share a microsecond).
    job = next(
        j for j in reversed(matching) if j.idempotency_key.endswith(f":{mapping.desired_revision}")
    )
    case.mapping, case.job, case.payload = mapping, job, json.loads(job.payload_json)
    return case


async def _run(db, case):
    return await calendar_sync.run_calendar_sync(db, case.payload, case.job)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_planner_create_reschedule_cancel_and_replay_have_committed_outcomes(
    auth_client, db_session, remote, provider
):
    case = await _seed(auth_client, db_session, provider)
    assert case.mapping.status == "queued" and case.mapping.synced_revision == 0
    remote.put_event.assert_not_awaited()
    assert await _run(db_session, case) == {"synced": True, "removed": False}
    assert case.mapping.status == "synced" and case.mapping.synced_revision == 1
    assert case.mapping.event_id == "synthetic-remote-event"
    assert case.mapping.last_synced_at and case.conn.last_synced_at
    first = remote.put_event.await_args
    assert first.args[:4] == (provider, "synthetic-access", case.conn.calendar_id, None)
    assert first.args[4] == case.mapping.stable_id
    assert first.args[5]["summary"] == "CAL-WORK · Replace windows"
    assert first.args[5]["description"] == "Use the rear entrance"
    assert first.kwargs["allow_create"] is True
    assert await _run(db_session, case) == {"synced": True, "reason": "already_synced"}
    assert remote.put_event.await_count == 1

    changed = await auth_client.patch(
        f"/api/v1/schedule/assignments/{case.row.id}",
        json={
            "starts_at": "2026-10-25T08:00:00+00:00",
            "ends_at": "2026-10-25T12:00:00+00:00",
            "note": "Bring replacement hinges",
            "expected_revision": 1,
        },
    )
    assert changed.status_code == 200, changed.text
    await _queued(db_session, case)
    assert case.mapping.desired_revision == 2 and case.mapping.status == "queued"
    assert await _run(db_session, case) == {"synced": True, "removed": False}
    assert remote.put_event.await_count == 2
    updated = remote.put_event.await_args
    assert updated.args[3] == "synthetic-remote-event"
    assert updated.args[4] == first.args[4]
    assert updated.args[5]["start"] == {
        "dateTime": "2026-10-25T08:00:00+00:00",
        "timeZone": "UTC",
    }
    assert updated.args[5]["description"] == "Bring replacement hinges"

    cancelled = await auth_client.post(
        f"/api/v1/schedule/assignments/{case.row.id}/transition",
        json={"status": "cancelled", "expected_revision": 2},
    )
    assert cancelled.status_code == 200, cancelled.text
    await _queued(db_session, case)
    assert await _run(db_session, case) == {"synced": True, "removed": True}
    remote.delete_event.assert_awaited_once_with(
        provider, "synthetic-access", case.conn.calendar_id, "synthetic-remote-event"
    )
    assert case.mapping.event_id is None and case.mapping.synced_revision == 3
    await _run(db_session, case)
    assert remote.delete_event.await_count == 1 and remote.put_event.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_reassignment_removes_old_assignee_event_and_exports_to_current_owner(
    auth_client, db_session, remote, provider
):
    case = await _seed(auth_client, db_session, provider)
    await _run(db_session, case)
    old_conn = case.conn
    invite = await auth_client.post(
        "/api/v1/team/invites", json={"email": "crew-calendar@acme.io", "role": "user"}
    )
    assert invite.status_code in (200, 201), invite.text
    accepted = await auth_client.post(
        "/api/v1/auth/accept-invite",
        json={
            "token": invite.json()["token"],
            "name": "Crew",
            "password": "supersecret",
        },
    )
    assert accepted.status_code in (200, 201), accepted.text
    employee_id = accepted.json()["user"]["id"]
    new_conn = await _connection(db_session, old_conn.org_id, employee_id, provider)
    changed = await auth_client.patch(
        f"/api/v1/schedule/assignments/{case.row.id}",
        json={"assignee_user_id": employee_id, "expected_revision": 1},
    )
    assert changed.status_code == 200, changed.text
    await _queued(db_session, case)
    assert await _run(db_session, case) == {"synced": True, "removed": True}
    remote.delete_event.assert_awaited_once_with(
        provider, "synthetic-access", old_conn.calendar_id, "synthetic-remote-event"
    )
    await _queued(db_session, case, connection=new_conn)
    assert await _run(db_session, case) == {"synced": True, "removed": False}
    assert remote.put_event.await_count == 2
    assert remote.put_event.await_args.args[2] == new_conn.calendar_id
    assert case.mapping.connection_id == new_conn.id and case.mapping.synced_revision == 2

    # Returning to the original assignee must create one new managed event.
    # The old Google ID can remain a deletion tombstone indefinitely.
    old_mapping = await db_session.scalar(
        select(CalendarEventMapping).where(CalendarEventMapping.connection_id == old_conn.id)
    )
    retired_identity = remote.put_event.await_args_list[0].args[4]
    assert old_mapping.event_id is None and not old_mapping.create_started
    assert old_mapping.stable_id != retired_identity
    returned = await auth_client.patch(
        f"/api/v1/schedule/assignments/{case.row.id}",
        json={"assignee_user_id": case.owner["id"], "expected_revision": 2},
    )
    assert returned.status_code == 200, returned.text
    await _queued(db_session, case, connection=old_conn)
    assert await _run(db_session, case) == {"synced": True, "removed": False}
    assert remote.put_event.await_count == 3
    assert remote.put_event.await_args.args[4] != retired_identity
    assert remote.put_event.await_args.kwargs["allow_create"] is True
    assert await _run(db_session, case) == {"synced": True, "reason": "already_synced"}
    assert remote.put_event.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_lost_create_response_uses_readback_identity_before_recovery(
    auth_client, db_session, remote, provider
):
    case = await _seed(auth_client, db_session, provider)
    stable_id = case.mapping.stable_id
    remote.put_event.side_effect = providers.ProviderError("provider_timeout", ambiguous=True)
    with pytest.raises(RuntimeError, match="provider_timeout"):
        await _run(db_session, case)
    assert case.mapping.status == "unknown" and case.mapping.create_started
    assert case.mapping.synced_revision == 0 and case.mapping.last_synced_at is None
    assert case.mapping.event_id is None and case.mapping.lease_owner is None
    remote.find_event.return_value = "event-committed-before-lost-response"
    remote.put_event.side_effect = None
    remote.put_event.return_value = "event-committed-before-lost-response"
    assert await _run(db_session, case) == {"synced": True, "removed": False}
    recovered = remote.put_event.await_args
    assert recovered.args[3:5] == ("event-committed-before-lost-response", stable_id)
    assert recovered.kwargs["allow_create"] is False
    assert case.mapping.event_id == "event-committed-before-lost-response"
    assert case.mapping.synced_revision == 1 and case.mapping.status == "synced"
    await _run(db_session, case)
    assert remote.put_event.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_cancelling_unknown_create_requires_readback_before_retiring_identity(
    auth_client, db_session, remote, provider
):
    case = await _seed(auth_client, db_session, provider)
    original_identity = case.mapping.stable_id
    remote.put_event.side_effect = providers.ProviderError("provider_timeout", ambiguous=True)
    with pytest.raises(RuntimeError, match="provider_timeout"):
        await _run(db_session, case)
    response = await auth_client.post(
        f"/api/v1/schedule/assignments/{case.row.id}/transition",
        json={"status": "cancelled", "expected_revision": 1},
    )
    assert response.status_code == 200, response.text
    await _queued(db_session, case)
    with pytest.raises(RuntimeError, match="event_removal_unknown"):
        await _run(db_session, case)
    assert case.mapping.status == "unknown" and case.mapping.synced_revision == 0
    assert case.mapping.create_started and case.mapping.stable_id == original_identity
    remote.delete_event.assert_not_awaited()
    remote.find_event.return_value = "late-provider-event"
    assert await _run(db_session, case) == {"synced": True, "removed": True}
    remote.delete_event.assert_awaited_once_with(
        provider, "synthetic-access", case.conn.calendar_id, "late-provider-event"
    )
    assert case.mapping.stable_id != original_identity and not case.mapping.create_started
    assert case.mapping.event_id is None and case.mapping.synced_revision == 2
    assert await _run(db_session, case) == {"synced": True, "reason": "already_synced"}
    assert remote.put_event.await_count == 1


@pytest.mark.asyncio
async def test_unconfirmed_create_stays_unknown_without_authorizing_another_create(
    auth_client, db_session, remote
):
    case = await _seed(auth_client, db_session, "outlook")
    remote.put_event.side_effect = providers.ProviderError("provider_timeout", ambiguous=True)
    with pytest.raises(RuntimeError):
        await _run(db_session, case)
    remote.put_event.side_effect = providers.ProviderError(
        "event_creation_unconfirmed", ambiguous=True
    )
    with pytest.raises(RuntimeError, match="event_creation_unconfirmed"):
        await _run(db_session, case)
    assert remote.put_event.await_args.kwargs["allow_create"] is False
    assert case.mapping.status == "unknown" and case.mapping.synced_revision == 0
    assert case.conn.last_synced_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_future_expiry_401_refreshes_once_then_persists_new_sealed_credentials(
    auth_client, db_session, remote, provider
):
    case = await _seed(auth_client, db_session, provider)
    remote.put_event.side_effect = [
        providers.ProviderError("authorization_expired", auth_required=True),
        "event-after-refresh",
    ]
    assert await _run(db_session, case) == {"synced": True, "removed": False}
    remote.refresh_tokens.assert_awaited_once_with(provider, "synthetic-refresh")
    assert [call.args[1] for call in remote.put_event.await_args_list] == [
        "synthetic-access",
        "fresh-synthetic-access",
    ]
    assert "fresh-synthetic-access" not in case.conn.sealed_tokens
    plaintext = keyvault.unseal(case.conn.sealed_tokens, aad=calendar_sync._aad(case.conn))
    assert json.loads(plaintext)["access_token"] == "fresh-synthetic-access"
    assert case.mapping.status == "synced" and case.conn.status == "connected"


@pytest.mark.asyncio
async def test_second_401_requires_reconnect_instead_of_looping(auth_client, db_session, remote):
    case = await _seed(auth_client, db_session)
    remote.put_event.side_effect = providers.ProviderError(
        "authorization_expired", auth_required=True
    )
    with pytest.raises(jobs.PermanentJobError, match="authorization_expired"):
        await _run(db_session, case)
    assert remote.refresh_tokens.await_count == 1 and remote.put_event.await_count == 2
    assert case.conn.status == "needs_reconnect"
    assert case.conn.error == "calendar_authorization_expired"
    assert case.mapping.status == "failed" and case.mapping.synced_revision == 0
    assert await _run(db_session, case) == {"synced": False, "reason": "disconnected"}
    assert remote.put_event.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_failure", ["tampered", "expired", "wrong_generation"])
async def test_unusable_credentials_require_reconnect_and_do_not_write_events(
    auth_client, db_session, remote, credential_failure
):
    case = await _seed(auth_client, db_session)
    if credential_failure == "tampered":
        case.conn.sealed_tokens = "kv1.invalid-ciphertext"
    elif credential_failure == "wrong_generation":
        case.conn.generation += 1
        case.payload["generation"] = case.conn.generation
    else:
        case.conn.sealed_tokens = keyvault.seal(
            json.dumps(
                {
                    "access_token": "expired-synthetic-access",
                    "refresh_token": "revoked-synthetic-refresh",
                    "expires_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                }
            ),
            aad=calendar_sync._aad(case.conn),
        )
    remote.refresh_tokens.side_effect = providers.ProviderError(
        "authorization_expired", auth_required=True
    )
    await db_session.commit()
    with pytest.raises(jobs.PermanentJobError):
        await _run(db_session, case)
    assert case.conn.status == "needs_reconnect"
    assert case.mapping.synced_revision == 0 and case.mapping.last_synced_at is None
    remote.put_event.assert_not_awaited()
    remote.delete_event.assert_not_awaited()
    if credential_failure != "expired":
        remote.refresh_tokens.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revocation", ["membership", "user", "organization", "generation", "disconnect"]
)
async def test_revoked_or_replaced_connection_never_contacts_calendar_provider(
    auth_client, db_session, remote, revocation
):
    case = await _seed(auth_client, db_session)
    if revocation == "membership":
        member = await db_session.scalar(
            select(Membership).where(
                Membership.org_id == case.conn.org_id,
                Membership.user_id == case.conn.user_id,
            )
        )
        member.status = "suspended"
    elif revocation == "user":
        user = await db_session.get(User, case.conn.user_id)
        user.is_active = False
    elif revocation == "organization":
        org = await db_session.get(Organization, case.conn.org_id)
        org.status = "suspended"
    elif revocation == "generation":
        case.conn.generation += 1
    else:
        await calendar_sync.disconnect(
            db_session, case.conn.org_id, case.conn.user_id, case.conn.provider
        )
    await db_session.commit()
    result = await _run(db_session, case)
    assert result["synced"] is False
    for mock in vars(remote).values():
        mock.assert_not_awaited()
    assert case.mapping.synced_revision == 0
    if revocation in {"membership", "user", "organization"}:
        assert result["reason"] == "access_revoked"
        assert case.conn.status == "disconnected" and case.conn.sealed_tokens is None


@pytest.mark.asyncio
async def test_revocation_after_intent_commit_is_checked_before_remote_write(
    auth_client, db_session, remote, monkeypatch
):
    case = await _seed(auth_client, db_session)
    checks = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(calendar_sync, "_active_owner", checks)
    assert await _run(db_session, case) == {"synced": False, "reason": "access_revoked"}
    assert checks.await_count == 2
    assert case.mapping.status == "blocked" and case.mapping.error == "access_revoked"
    assert case.mapping.lease_owner is None and case.mapping.lease_until is None
    for mock in vars(remote).values():
        mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_mapping_lease_defers_a_competing_worker_without_provider_io(
    auth_client, db_session, remote
):
    case = await _seed(auth_client, db_session)
    case.mapping.lease_owner = str(uuid.uuid4())
    case.mapping.lease_until = datetime.now(UTC) + timedelta(minutes=2)
    await db_session.commit()
    with pytest.raises(jobs.RetryAfterError) as exc:
        await _run(db_session, case)
    assert exc.value.retry_after_seconds == 30
    assert case.mapping.lease_owner != case.job.id
    for mock in vars(remote).values():
        mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_removes_assignment_export_intent_and_mapping(
    auth_client, db_session, remote
):
    case = await _seed(auth_client, db_session, queue=False)
    assignment_id = case.row.id
    original_note = case.row.note
    # Flush a real planner write before the savepoint-backed idempotent insert.
    case.row.note = "This planner transaction will fail"
    case.row.revision += 1
    await db_session.flush()
    await calendar_sync.enqueue_assignment_sync(db_session, case.row)
    assert await db_session.scalar(select(func.count()).select_from(CalendarEventMapping)) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == "calendar.sync")
        )
        == 1
    )
    await db_session.rollback()
    row = await db_session.get(ProjectAssignment, assignment_id, populate_existing=True)
    assert row.note == original_note and row.revision == 1
    assert await db_session.scalar(select(func.count()).select_from(CalendarEventMapping)) == 0
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == "calendar.sync")
        )
        == 0
    )
    for mock in vars(remote).values():
        mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_all_day_export_uses_local_dates_across_dst_not_utc_dates(
    auth_client, db_session, remote, provider
):
    case = await _seed(
        auth_client,
        db_session,
        provider,
        starts_at="2026-10-24T21:00:00+00:00",
        ends_at="2026-10-25T22:00:00+00:00",
        all_day=True,
        time_zone="Europe/Riga",
    )
    assert await _run(db_session, case) == {"synced": True, "removed": False}
    payload = remote.put_event.await_args.args[5]
    assert payload["start"] == {"date": "2026-10-25", "timeZone": "Europe/Riga"}
    assert payload["end"] == {"date": "2026-10-26", "timeZone": "Europe/Riga"}
    assert "attendees" not in payload


@pytest.mark.asyncio
async def test_timed_export_preserves_configured_zone_and_authoritative_timestamps(
    auth_client, db_session, remote
):
    case = await _seed(auth_client, db_session, time_zone="Europe/Riga")
    await _run(db_session, case)
    payload = remote.put_event.await_args.args[5]
    assert payload["start"] == {
        "dateTime": "2026-10-24T09:00:00+00:00",
        "timeZone": "Europe/Riga",
    }
    assert payload["end"] == {
        "dateTime": "2026-10-24T17:00:00+00:00",
        "timeZone": "Europe/Riga",
    }


@pytest.mark.asyncio
async def test_deleted_assignment_tombstone_removes_remote_event(auth_client, db_session, remote):
    case = await _seed(auth_client, db_session)
    await _run(db_session, case)
    case.mapping.desired_revision += 1
    case.mapping.status = "queued"
    await db_session.delete(case.row)
    await db_session.commit()
    assert await _run(db_session, case) == {"synced": True, "removed": True}
    remote.delete_event.assert_awaited_once()
    assert case.mapping.event_id is None and case.mapping.synced_revision == 2


@pytest.mark.asyncio
async def test_provider_backoff_persists_failed_outcome_and_keeps_retry_delay(
    auth_client, db_session, remote
):
    case = await _seed(auth_client, db_session)
    remote.put_event.side_effect = providers.ProviderError("provider_busy", retry_after=123)
    with pytest.raises(jobs.RetryAfterError) as exc:
        await _run(db_session, case)
    assert exc.value.retry_after_seconds == 123
    assert case.mapping.status == "failed" and case.mapping.error == "provider_busy"
    assert case.mapping.synced_revision == 0 and not case.mapping.create_started
    assert case.mapping.lease_owner is None and case.conn.last_synced_at is None


@pytest.mark.asyncio
async def test_enqueue_replay_keeps_one_mapping_and_one_job_for_the_revision(
    auth_client, db_session, remote
):
    case = await _seed(auth_client, db_session)
    await calendar_sync.enqueue_assignment_sync(db_session, case.row)
    await calendar_sync.enqueue_assignment_sync(db_session, case.row)
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(CalendarEventMapping)) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == "calendar.sync")
        )
        == 1
    )
    for mock in vars(remote).values():
        mock.assert_not_awaited()
    assert await _run(db_session, case) == {"synced": True, "removed": False}
    assert remote.put_event.await_count == 1


@pytest.mark.asyncio
async def test_older_job_exports_current_revision_and_newer_job_reads_committed_outcome(
    auth_client, db_session, remote
):
    case = await _seed(auth_client, db_session)
    original_job, original_payload = case.job, dict(case.payload)
    response = await auth_client.patch(
        f"/api/v1/schedule/assignments/{case.row.id}",
        json={"note": "Current instructions supersede the queued draft", "expected_revision": 1},
    )
    assert response.status_code == 200, response.text
    await _queued(db_session, case)
    assert case.job.id != original_job.id and case.mapping.desired_revision == 2
    result = await calendar_sync.run_calendar_sync(db_session, original_payload, original_job)
    assert result == {"synced": True, "removed": False}
    assert remote.put_event.await_args.args[5]["description"] == (
        "Current instructions supersede the queued draft"
    )
    assert case.mapping.synced_revision == 2
    assert await _run(db_session, case) == {"synced": True, "reason": "already_synced"}
    assert remote.put_event.await_count == 1


@pytest.mark.asyncio
async def test_job_from_another_tenant_cannot_export_a_known_connection(
    auth_client, db_session, remote
):
    case = await _seed(auth_client, db_session)
    foreign_job = Job(id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), kind="calendar.sync")
    assert await calendar_sync.run_calendar_sync(db_session, case.payload, foreign_job) == {
        "synced": False,
        "reason": "disconnected",
    }
    assert case.mapping.status == "queued" and case.conn.status == "connected"
    for mock in vars(remote).values():
        mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_event_revision_conflict_preserves_mapped_remote_event_for_safe_retry(
    auth_client, db_session, remote, provider
):
    case = await _seed(auth_client, db_session, provider)
    await _run(db_session, case)
    acknowledged_event = case.mapping.event_id
    acknowledged_at = case.mapping.last_synced_at
    changed = await auth_client.post(
        f"/api/v1/schedule/assignments/{case.row.id}/transition",
        json={"status": "cancelled", "expected_revision": 1},
    )
    assert changed.status_code == 200, changed.text
    await _queued(db_session, case)
    remote.delete_event.side_effect = providers.ProviderError("provider_event_changed")
    with pytest.raises(RuntimeError, match="provider_event_changed"):
        await _run(db_session, case)
    assert case.mapping.event_id == acknowledged_event
    assert case.mapping.synced_revision == 1 and case.mapping.desired_revision == 2
    assert case.mapping.last_synced_at.replace(tzinfo=UTC) == acknowledged_at
    assert case.mapping.status == "failed" and case.mapping.error == "provider_event_changed"
    assert case.mapping.lease_owner is None and case.conn.status == "connected"
    assert remote.delete_event.await_count == 1
    assert remote.put_event.await_count == 1
