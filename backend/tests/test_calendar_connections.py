"""OAuth credentials, browser commands, and uncertain calendar setup contracts."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import delete, select, update

from app.api.routes import calendar_connections
from app.core import keyvault
from app.core.config import settings
from app.models.calendar_connection import (
    CalendarConnection,
    CalendarEventMapping,
    CalendarOAuthAttempt,
)
from app.models.job import Job
from app.services import calendar_providers as providers
from app.services import calendar_sync

BASE = "/api/v1/schedule/calendar-connections"


@pytest.fixture
def configured(monkeypatch):
    for provider in ("google", "outlook"):
        monkeypatch.setattr(settings, f"{provider}_calendar_client_id", "synthetic-id")
        monkeypatch.setattr(settings, f"{provider}_calendar_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "api_public_base_url", "http://localhost:8000")
    monkeypatch.setattr(settings, "calendar_public_app_url", "http://localhost:5173")
    monkeypatch.setattr(settings, "environment", "development")


def provider_mocks(monkeypatch):
    mocks = {
        "exchange_code": AsyncMock(
            return_value={
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "expires_in": 3600,
            }
        ),
        "account_key": AsyncMock(return_value="synthetic-account"),
        "find_calendar": AsyncMock(return_value=None),
        "create_calendar": AsyncMock(return_value="synthetic-calendar"),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(providers, name, mock)
    return mocks


async def begin(client, provider="google"):
    response = await client.post(f"{BASE}/{provider}/connect")
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    return response, query["state"][0]


async def callback(client, state, provider="google"):
    return await client.get(
        f"{BASE}/{provider}/callback",
        params={"state": state, "code": "synthetic-code"},
        follow_redirects=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_oauth_seals_tokens_claims_once_and_reconciles(
    auth_client, db_session, configured, monkeypatch, provider
):
    mocks = provider_mocks(monkeypatch)
    response, state = await begin(auth_client, provider)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=600" in cookie
    attempt = await db_session.scalar(select(CalendarOAuthAttempt))
    assert attempt.state_hash != state and state not in attempt.sealed_verifier
    verifier = keyvault.unseal(attempt.sealed_verifier, aad=calendar_sync._attempt_aad(attempt))
    assert len(verifier) >= 43
    done = await callback(auth_client, state, provider)
    assert done.status_code == 303
    assert done.headers["location"].endswith("calendar_sync=connected")
    assert done.headers["cache-control"] == "no-store"
    assert done.headers["referrer-policy"] == "no-referrer"
    conn = await db_session.scalar(
        select(CalendarConnection).execution_options(populate_existing=True)
    )
    assert conn.status == "connected" and conn.calendar_id == "synthetic-calendar"
    assert "synthetic-access" not in conn.sealed_tokens
    assert "synthetic-refresh" not in conn.sealed_tokens
    tokens = json.loads(keyvault.unseal(conn.sealed_tokens, aad=calendar_sync._aad(conn)))
    assert tokens["access_token"] == "synthetic-access"
    assert uuid.UUID(conn.calendar_marker)
    assert mocks["create_calendar"].call_args.kwargs["name"] == "Acme work schedule"
    assert mocks["create_calendar"].call_args.kwargs["marker"] == conn.calendar_marker
    assert await db_session.scalar(select(Job.id).where(Job.kind == "calendar.sync"))
    replay = await callback(auth_client, state, provider)
    assert replay.headers["location"].endswith("calendar_sync=failed")
    assert mocks["exchange_code"].await_count == 1
    current = await auth_client.get(BASE)
    body = current.text
    for private in (
        "synthetic-access",
        "synthetic-refresh",
        "synthetic-secret",
        conn.calendar_marker,
        conn.account_hash,
    ):
        assert private not in body


@pytest.mark.asyncio
async def test_expired_oauth_and_decline_do_not_exchange(
    auth_client, db_session, configured, monkeypatch
):
    mocks = provider_mocks(monkeypatch)
    _, state = await begin(auth_client)
    await db_session.execute(
        update(CalendarOAuthAttempt).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    result = await callback(auth_client, state)
    assert result.headers["location"].endswith("calendar_sync=failed")
    assert mocks["exchange_code"].await_count == 0
    _, state = await begin(auth_client)
    declined = await auth_client.get(
        f"{BASE}/google/callback",
        params={"state": state, "error": "synthetic-secret-error"},
        follow_redirects=False,
    )
    assert "synthetic-secret-error" not in declined.text + str(declined.headers)
    assert declined.headers["location"].endswith("calendar_sync=failed")
    assert mocks["exchange_code"].await_count == 0


@pytest.mark.asyncio
async def test_disconnect_during_exchange_cannot_restore_credentials(
    auth_client, db_session, configured, monkeypatch
):
    mocks = provider_mocks(monkeypatch)
    _, state = await begin(auth_client)
    conn = await db_session.scalar(select(CalendarConnection))
    original_generation = conn.generation

    async def exchange(*_args):
        await calendar_sync.disconnect(db_session, conn.org_id, conn.user_id, "google")
        return {
            "access_token": "old-response-access",
            "refresh_token": "old-response-refresh",
            "expires_in": 3600,
        }

    monkeypatch.setattr(providers, "exchange_code", exchange)
    result = await callback(auth_client, state)
    assert result.headers["location"].endswith("calendar_sync=failed")
    await db_session.refresh(conn)
    assert conn.generation == original_generation + 1
    assert conn.status == "disconnected" and conn.sealed_tokens is None
    mocks["create_calendar"].assert_not_awaited()


@pytest.mark.asyncio
async def test_tampered_pkce_requires_new_authorization(
    auth_client, db_session, configured, monkeypatch
):
    mocks = provider_mocks(monkeypatch)
    _, state = await begin(auth_client)
    await db_session.execute(
        update(CalendarOAuthAttempt).values(sealed_verifier="invalid-sealed-verifier")
    )
    await db_session.commit()
    result = await callback(auth_client, state)
    assert result.status_code == 303 and result.headers["location"].endswith("calendar_sync=failed")
    mocks["exchange_code"].assert_not_awaited()


@pytest.mark.asyncio
async def test_different_calendar_account_requires_explicit_disconnect(
    auth_client, db_session, configured, monkeypatch
):
    mocks = provider_mocks(monkeypatch)
    _, state = await begin(auth_client)
    assert (
        (await callback(auth_client, state)).headers["location"].endswith("calendar_sync=connected")
    )
    conn = await db_session.scalar(select(CalendarConnection))
    original_tokens, original_generation = conn.sealed_tokens, conn.generation
    mocks["account_key"].return_value = "different-provider-account"
    _, state = await begin(auth_client)
    result = await callback(auth_client, state)
    assert result.headers["location"].endswith("calendar_sync=failed")
    await db_session.refresh(conn)
    assert conn.generation == original_generation and conn.sealed_tokens == original_tokens


@pytest.mark.asyncio
async def test_unknown_calendar_create_is_not_blindly_repeated(
    auth_client, db_session, configured, monkeypatch
):
    mocks = provider_mocks(monkeypatch)
    mocks["create_calendar"].side_effect = providers.ProviderError(
        "provider_timeout", ambiguous=True
    )
    _, state = await begin(auth_client)
    result = await callback(auth_client, state)
    assert result.headers["location"].endswith("calendar_sync=pending")
    conn = await db_session.scalar(select(CalendarConnection))
    assert conn.status == "setup_unknown" and conn.setup_started
    assert (await auth_client.post(f"{BASE}/google/retry")).status_code == 202
    out = await calendar_sync._finish_setup(db_session, conn.id, conn.org_id, conn.generation)
    assert out["reason"] == "calendar_setup_unknown"
    assert mocks["create_calendar"].await_count == 1
    mocks["find_calendar"].return_value = "provider-found-calendar"
    out = await calendar_sync._finish_setup(db_session, conn.id, conn.org_id, conn.generation)
    assert out["synced"] and conn.calendar_id == "provider-found-calendar"
    assert mocks["create_calendar"].await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_phase", ["before_setup", "after_intent_commit"])
async def test_setup_reloads_warmed_connection_after_external_disconnect(
    auth_client, db_session, _db, configured, monkeypatch, disconnect_phase
):
    mocks = provider_mocks(monkeypatch)
    await begin(auth_client)
    conn = await db_session.scalar(select(CalendarConnection))
    conn.status = "connecting"
    calendar_sync._seal_tokens(
        conn,
        {
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "expires_in": 3600,
        },
    )
    await db_session.commit()
    connection_id, org_id, generation = conn.id, conn.org_id, conn.generation

    async def external_disconnect():
        async with _db() as other:
            await other.execute(
                update(CalendarConnection)
                .where(CalendarConnection.id == connection_id)
                .values(status="disconnected", sealed_tokens=None, generation=generation + 1)
            )
            await other.commit()

    if disconnect_phase == "before_setup":
        await external_disconnect()
        assert conn.status == "connecting", (
            "The original session must retain stale data for this regression"
        )
    else:
        original_commit = db_session.commit
        calls = 0

        async def disconnect_after_commit():
            nonlocal calls
            await original_commit()
            calls += 1
            if calls == 1:
                await external_disconnect()

        monkeypatch.setattr(db_session, "commit", disconnect_after_commit)
    result = await calendar_sync._finish_setup(db_session, connection_id, org_id, generation)
    assert result == {"synced": False, "reason": "disconnected"}
    assert conn.status == "disconnected" and conn.sealed_tokens is None
    mocks["find_calendar"].assert_not_awaited()
    mocks["create_calendar"].assert_not_awaited()


@pytest.mark.asyncio
async def test_operator_configuration_is_truthful_and_blocks_connect(auth_client, monkeypatch):
    for provider in ("google", "outlook"):
        monkeypatch.setattr(settings, f"{provider}_calendar_client_id", None)
        monkeypatch.setattr(settings, f"{provider}_calendar_client_secret", None)
    response = await auth_client.get(BASE)
    assert all(not item["configured"] and item["reason"] for item in response.json()["providers"])
    assert (await auth_client.post(f"{BASE}/google/connect")).status_code == 400
    assert (await auth_client.post(f"{BASE}/outlook/retry")).status_code == 409


def test_cookie_path_and_public_url_configuration_respect_prefix(configured, monkeypatch):
    monkeypatch.setattr(settings, "api_public_base_url", "https://api.synthetic.invalid/backend")
    monkeypatch.setattr(settings, "api_v1_prefix", "/custom/api")
    assert (
        calendar_connections._cookie_path() == "/backend/custom/api/schedule/calendar-connections"
    )
    assert (
        calendar_sync.callback_uri("google")
        == "https://api.synthetic.invalid/backend/custom/api/schedule/calendar-connections/google/callback"
    )
    monkeypatch.setattr(settings, "environment", "production")
    assert calendar_sync.configured_reason("google")
    monkeypatch.setattr(settings, "calendar_public_app_url", "https://app.synthetic.invalid")
    assert calendar_sync.configured_reason("google") is None
    monkeypatch.setattr(
        settings,
        "calendar_public_app_url",
        "https://app.synthetic.invalid?return=https://evil.invalid",
    )
    assert calendar_sync.configured_reason("google")


@pytest.mark.asyncio
async def test_setup_marker_crosses_real_provider_adapter(
    auth_client, db_session, configured, monkeypatch
):
    monkeypatch.setattr(
        providers,
        "exchange_code",
        AsyncMock(
            return_value={
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "expires_in": 3600,
            }
        ),
    )
    monkeypatch.setattr(providers, "account_key", AsyncMock(return_value="synthetic-account"))
    posted = []

    def transport(request):
        if request.method == "GET":
            return httpx.Response(200, stream=httpx.ByteStream(b'{"items": []}'))
        body = json.loads(request.content)
        posted.append(body)
        return httpx.Response(200, stream=httpx.ByteStream(b'{"id": "real-adapter-calendar"}'))

    monkeypatch.setattr(
        providers, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport))
    )
    _, state = await begin(auth_client)
    result = await callback(auth_client, state)
    assert result.headers["location"].endswith("calendar_sync=connected")
    assert len(posted) == 1 and posted[0]["summary"] == "Acme work schedule"
    conn = await db_session.scalar(select(CalendarConnection))
    assert conn.calendar_marker in posted[0]["description"]


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["setup", "reconcile"])
async def test_retry_coalesces_active_generation_but_recovers_after_terminal_failure(
    auth_client, db_session, configured, monkeypatch, purpose
):
    provider_mocks(monkeypatch)
    _, state = await begin(auth_client)
    await callback(auth_client, state)
    conn = await db_session.scalar(select(CalendarConnection))
    await db_session.execute(delete(Job).where(Job.kind == calendar_sync.SYNC_JOB))
    if purpose == "setup":
        conn.calendar_id, conn.status = None, "setup_unknown"
    # An unfinished prior-generation chain must never absorb a current retry.
    old = Job(
        org_id=conn.org_id,
        kind=calendar_sync.SYNC_JOB,
        status="queued",
        payload_json=json.dumps(
            {"connection_id": conn.id, "generation": conn.generation - 1, purpose: True}
        ),
        idempotency_key=f"calendar.{purpose}:{conn.id}:{conn.generation - 1}:old",
    )
    db_session.add(old)
    await db_session.commit()
    for _ in range(3):
        result = await auth_client.post(f"{BASE}/google/retry")
        assert result.status_code == 202, result.text
    prefix = f"calendar.{purpose}:{conn.id}:{conn.generation}:"
    rows = list(await db_session.scalars(select(Job).where(Job.idempotency_key.like(prefix + "%"))))
    assert len(rows) == 1 and rows[0].status == "queued"
    rows[0].status = "failed"
    await db_session.commit()
    assert (await auth_client.post(f"{BASE}/google/retry")).status_code == 202
    rows = list(
        await db_session.scalars(
            select(Job)
            .where(Job.idempotency_key.like(prefix + "%"))
            .execution_options(populate_existing=True)
        )
    )
    assert len(rows) == 2 and sorted(row.status for row in rows) == ["failed", "queued"]


@pytest.mark.asyncio
async def test_retry_coalesces_unfinished_paginated_reconciliation(
    auth_client, db_session, configured, monkeypatch
):
    provider_mocks(monkeypatch)
    _, state = await begin(auth_client)
    await callback(auth_client, state)
    conn = await db_session.scalar(select(CalendarConnection))
    await db_session.execute(delete(Job).where(Job.kind == calendar_sync.SYNC_JOB))
    for _ in range(3):
        db_session.add(
            CalendarEventMapping(
                org_id=conn.org_id,
                connection_id=conn.id,
                assignment_id=str(uuid.uuid4()),
                stable_id=str(uuid.uuid4()),
                status="synced",
                desired_revision=1,
                synced_revision=1,
            )
        )
    await db_session.commit()
    monkeypatch.setattr(calendar_sync, "PAGE_SIZE", 2)
    assert (await auth_client.post(f"{BASE}/google/retry")).status_code == 202
    prefix = f"calendar.reconcile:{conn.id}:{conn.generation}:"
    job = await db_session.scalar(select(Job).where(Job.idempotency_key.like(prefix + "%")))
    result = await calendar_sync.run_calendar_sync(db_session, json.loads(job.payload_json), job)
    assert result == {"queued": 2, "continued": True}
    job.status = "succeeded"
    await db_session.commit()
    assert (await auth_client.post(f"{BASE}/google/retry")).status_code == 202
    chains = list(
        await db_session.scalars(select(Job).where(Job.idempotency_key.like(prefix + "%")))
    )
    assert len(chains) == 2
    continuation = next(row for row in chains if row.id != job.id)
    assert json.loads(continuation.payload_json)["cursor"]
    assert json.loads(continuation.payload_json)["generation"] == conn.generation
