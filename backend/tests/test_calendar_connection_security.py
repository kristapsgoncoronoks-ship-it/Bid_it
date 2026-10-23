"""A calendar provider return cannot follow an originating session into another org."""

from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import settings
from app.models.organization import Organization
from app.models.user import UserRole
from app.services import calendar_providers, calendar_sync, memberships


@pytest.mark.asyncio
async def test_oauth_callback_refuses_a_session_switched_to_another_workspace(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "google_calendar_client_id", "synthetic-client")
    monkeypatch.setattr(settings, "google_calendar_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "api_public_base_url", "https://api.calendar.example")
    monkeypatch.setattr(settings, "calendar_public_app_url", "https://calendar.example")
    me = (await auth_client.get("/api/v1/auth/me")).json()
    beta = Organization(name="Synthetic second calendar workspace")
    db_session.add(beta)
    await db_session.flush()
    await memberships.ensure(
        db_session, org_id=beta.id, user_id=me["user"]["id"], role=UserRole.user
    )
    await db_session.commit()
    exchanged = []

    async def must_not_exchange(*args, **kwargs):
        exchanged.append((args, kwargs))
        pytest.fail("A session moved to another workspace must not exchange calendar credentials")

    monkeypatch.setattr(calendar_providers, "exchange_code", must_not_exchange)
    started = await auth_client.post("/api/v1/schedule/calendar-connections/google/connect")
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    cookie = SimpleCookie()
    cookie.load(started.headers["set-cookie"])
    binding = cookie[calendar_sync.OAUTH_COOKIE].value
    assert (await auth_client.post(f"/api/v1/auth/switch-org/{beta.id}")).status_code == 200
    callback = await auth_client.get(
        "/api/v1/schedule/calendar-connections/google/callback",
        params={"state": state, "code": "synthetic-code-never-exchanged"},
        # A real provider redirect has its binding cookie, not a bearer header.
        headers={"Authorization": "", "Cookie": f"{calendar_sync.OAUTH_COOKIE}={binding}"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "https://calendar.example/schedule?calendar_sync=failed"
    assert not exchanged
    current = await auth_client.get("/api/v1/auth/me")
    assert current.json()["organization"]["id"] == beta.id
    status = await auth_client.get("/api/v1/schedule/calendar-connections")
    assert status.status_code == 200, status.text
    assert all(p["connection"] is None for p in status.json()["providers"])
