"""Login CSRF/replay regressions: real signing/JWKS, only external IdP I/O mocked."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.main import app
from app.models.organization import Organization
from app.models.session import Session
from app.models.sso_login_attempt import SsoLoginAttempt
from app.services import oidc, sso_transactions
from tests.test_sso_oidc import _connection
from tests.test_sso_oidc import _patch_network as _patch_network


async def begin(client, db, patch, *, create_connection=True, https=False):
    if create_connection:
        org_id = await db.scalar(select(Organization.id))
        await _connection(db, org_id)
    response = await client.get(
        ("https://test" if https else "") + "/api/v1/auth/sso/acme/authorize",
        follow_redirects=False,
    )
    assert response.status_code == 302
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    patch(nonce=oidc.read_state(state)["nonce"])
    name = sso_transactions.cookie_name(state)
    secret = client.cookies.get(name)
    assert secret is not None
    return state, name, secret, response


@pytest.mark.asyncio
async def test_callback_from_another_browser_is_refused_before_exchange(
    auth_client, db_session, _patch_network, monkeypatch
):
    state, name, secret, _ = await begin(auth_client, db_session, _patch_network)
    calls = []
    original = oidc.exchange_code

    async def exchange(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(oidc, "exchange_code", exchange)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        refused = await other.get(
            "/api/v1/auth/sso/callback",
            params={"state": state, "code": "synthetic-code"},
            follow_redirects=False,
        )
    assert "sso_error=1" in refused.headers["location"]
    assert "access_token" not in refused.headers["location"]
    assert calls == []
    # An attacker without the cookie cannot consume the legitimate transaction.
    accepted = await auth_client.get(
        "/api/v1/auth/sso/callback",
        params={"state": state, "code": "synthetic-code"},
        follow_redirects=False,
    )
    assert "#access_token=" in accepted.headers["location"] and calls == [True]


@pytest.mark.asyncio
async def test_callback_replay_with_original_cookie_still_cannot_issue_a_second_session(
    auth_client, db_session, _patch_network
):
    state, name, secret, _ = await begin(auth_client, db_session, _patch_network)
    before = await db_session.scalar(select(func.count()).select_from(Session))
    first = await auth_client.get(
        "/api/v1/auth/sso/callback",
        params={"state": state, "code": "first"},
        follow_redirects=False,
    )
    assert "#access_token=" in first.headers["location"]
    again = await auth_client.get(
        "/api/v1/auth/sso/callback",
        params={"state": state, "code": "second"},
        headers={"Cookie": f"{name}={secret}"},
        follow_redirects=False,
    )
    assert "sso_error=1" in again.headers["location"]
    assert await db_session.scalar(select(func.count()).select_from(Session)) == before + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["wrong_cookie", "expired", "provider_error"])
async def test_invalid_or_canceled_transactions_never_issue_sessions(
    auth_client, db_session, _patch_network, failure
):
    state, name, secret, _ = await begin(auth_client, db_session, _patch_network)
    before = await db_session.scalar(select(func.count()).select_from(Session))
    if failure == "expired":
        row = await db_session.scalar(select(SsoLoginAttempt))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.commit()
    params = {"state": state, "code": "synthetic-code"}
    headers = {}
    if failure == "wrong_cookie":
        headers = {"Cookie": f"{name}=incorrect"}
    if failure == "provider_error":
        params = {"state": state, "error": "access_denied"}
    response = await auth_client.get(
        "/api/v1/auth/sso/callback", params=params, headers=headers, follow_redirects=False
    )
    assert "sso_error=1" in response.headers["location"]
    assert await db_session.scalar(select(func.count()).select_from(Session)) == before
    assert auth_client.cookies.get(name) is None
    if failure == "provider_error":
        row = await db_session.scalar(
            select(SsoLoginAttempt).execution_options(populate_existing=True)
        )
        assert row.consumed_at is not None


@pytest.mark.asyncio
async def test_concurrent_login_tabs_keep_independent_cookies_and_secure_attributes(
    auth_client, db_session, _patch_network
):
    one, name_one, secret, response = await begin(
        auth_client, db_session, _patch_network, https=True
    )
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "secure" in header and "samesite=lax" in header
    assert "max-age=600" in header and "path=/api/v1/auth/sso/callback" in header
    two, name_two, _, _ = await begin(
        auth_client, db_session, _patch_network, create_connection=False, https=True
    )
    assert name_one != name_two
    for state, name, remaining in ((one, name_one, name_two), (two, name_two, None)):
        _patch_network(nonce=oidc.read_state(state)["nonce"])
        result = await auth_client.get(
            "https://test/api/v1/auth/sso/callback",
            params={"state": state, "code": "synthetic-code"},
            follow_redirects=False,
        )
        assert "#access_token=" in result.headers["location"]
        assert auth_client.cookies.get(name) is None
        if remaining:
            assert auth_client.cookies.get(remaining) is not None
