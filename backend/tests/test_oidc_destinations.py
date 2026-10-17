"""SYS-01: provider metadata must not redirect credentials into private networks."""

import socket
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select

from app.core import outbound_http
from app.models.session import Session
from app.models.sso_login_attempt import SsoLoginAttempt
from app.models.user import User
from app.services import oidc
from tests.test_sso_oidc import _connection, _keypair, _mint

ISSUER = "https://idp.example.test/realm"
PUBLIC_IP = "93.184.216.34"


def metadata(**overrides):
    return {
        "issuer": ISSUER,
        "authorization_endpoint": "https://login.example.test/auth",
        "token_endpoint": "https://tokens.example.test:8443/token?realm=one",
        "jwks_uri": "https://keys.example.test/jwks",
        **overrides,
    }


@pytest.fixture
def wire(monkeypatch):
    """Fake only socket transport; retain HTTPX and the actual address pin."""
    seen, payload = [], {"value": metadata(), "status": 200}

    async def receive(self, req):
        seen.append(req)
        await req.aread()
        return httpx.Response(payload["status"], json=payload["value"], request=req)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", receive)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))],
    )
    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", lambda *a: [PUBLIC_IP])
    return seen, payload


async def request(surface, url):
    if surface == "discovery":
        return await oidc.discover(url)
    if surface == "jwks":
        return await oidc.fetch_jwks(url)
    return await oidc.exchange_code(
        url,
        code="synthetic-code",
        redirect_uri="https://app.example.test/callback",
        client_id="synthetic-client",
        client_secret="synthetic-client-secret",
        code_verifier="synthetic-verifier",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/token",
        "https://127.0.0.1/token",
        "https://[::1]/token",
        "https://169.254.169.254/token",
        "https://10.0.0.1/token",
        "https://localhost/token",
        "https://service.localhost./token",
        "http://tokens.example.test/token",
        "ftp://tokens.example.test/token",
        "https://name:synthetic-password@tokens.example.test/token",
        "https://@tokens.example.test/token",
        "https://tokens.example.test/token#fragment",
        " https://tokens.example.test/token",
        "https://tokens.example.test/\\token",
    ],
)
async def test_token_refused_before_any_credential_transmission(wire, url):
    seen, _ = wire
    with pytest.raises(oidc.SsoError):
        await request("token", url)
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["discovery", "jwks", "token"])
@pytest.mark.parametrize("answers", [["10.0.0.1"], [PUBLIC_IP, "127.0.0.1"]])
async def test_every_seam_checks_connect_time_dns(monkeypatch, wire, surface, answers):
    seen, _ = wire
    # Legacy preflight DNS is public; connect-time answers must still win.
    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", lambda *a: answers)
    with pytest.raises(oidc.SsoError):
        await request(surface, ISSUER)
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["discovery", "jwks", "token"])
async def test_dns_failure_is_closed_without_transmission(monkeypatch, wire, surface):
    seen, _ = wire

    def unavailable(*args):
        raise httpx.ConnectError("synthetic DNS failure")

    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", unavailable)
    with pytest.raises(oidc.SsoError):
        await request(surface, ISSUER)
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["discovery", "jwks", "token"])
async def test_https_pins_address_preserves_sni_and_ignores_proxy(monkeypatch, wire, surface):
    seen, _ = wire
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    url = metadata()["token_endpoint"] if surface == "token" else ISSUER
    await request(surface, url)
    assert len(seen) == 1
    sent = seen[0]
    assert sent.url.host == PUBLIC_IP
    assert sent.extensions["sni_hostname"] == urlsplit(url).hostname
    assert sent.headers["host"] == urlsplit(url).netloc
    assert sent.url.query == httpx.URL(url).query
    if surface == "token":
        values = parse_qs(sent.content.decode())
        assert values["client_secret"] == ["synthetic-client-secret"]
        assert values["code_verifier"] == ["synthetic-verifier"]
        assert values["code"] == ["synthetic-code"]
        assert sent.url.port == 8443 and sent.method == "POST"
    else:
        assert sent.method == "GET"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 400, 500])
async def test_token_failure_does_not_echo_provider_response(wire, status):
    seen, payload = wire
    payload.update(status=status, value={"error": "synthetic-client-secret synthetic-code"})
    with pytest.raises(oidc.SsoError) as failure:
        await request("token", metadata()["token_endpoint"])
    assert "synthetic-client-secret" not in str(failure.value)
    assert "synthetic-code" not in str(failure.value)
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["discovery", "jwks", "token"])
async def test_redirect_refused_without_followup(wire, surface, monkeypatch):
    seen, _ = wire

    async def redirect(self, req):
        seen.append(req)
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/internal"}, request=req)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", redirect)
    with pytest.raises(oidc.SsoError):
        await request(surface, ISSUER)
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"issuer": "https://other.example.test/realm"},
        {"issuer": ISSUER + "/"},
        {"issuer": None},
        {"token_endpoint": "https://127.0.0.1/token"},
        {"authorization_endpoint": "http://login.example.test/auth"},
        {"jwks_uri": "https://user:pass@keys.example.test/jwks"},
        {"jwks_uri": None},
    ],
)
async def test_bad_metadata_refused_before_secret_access(monkeypatch, wire, change):
    _, payload = wire
    payload["value"] = metadata(**change)

    def must_not_unseal(*args, **kwargs):
        raise AssertionError("invalid metadata reached client-secret access")

    monkeypatch.setattr(oidc.keyvault, "read_secret", must_not_unseal)
    conn = SimpleNamespace(issuer=ISSUER, client_id="synthetic", client_secret="synthetic")
    with pytest.raises(oidc.SsoError):
        await oidc.finish_login(None, conn, code="synthetic", nonce="n", code_verifier="v")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [[], None, "unexpected"])
async def test_nonobject_response_is_safe_failure(wire, value):
    _, payload = wire
    payload["value"] = value
    with pytest.raises(oidc.SsoError):
        await request("jwks", metadata()["jwks_uri"])


@pytest.mark.asyncio
async def test_callback_rejects_changed_metadata_consumes_state_and_recovers(
    auth_client,
    db_session,
    wire,
):
    from app.models.organization import Organization

    seen, payload = wire
    org_id = await db_session.scalar(select(Organization.id))
    await _connection(db_session, org_id, issuer=ISSUER)
    before_users = await db_session.scalar(select(func.count()).select_from(User))
    before_sessions = await db_session.scalar(select(func.count()).select_from(Session))
    started = await auth_client.get("/api/v1/auth/sso/acme/authorize", follow_redirects=False)
    assert started.status_code == 302
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    payload["value"] = metadata(token_endpoint="https://127.0.0.1/internal")
    callback = await auth_client.get(
        "/api/v1/auth/sso/callback",
        params={"state": state, "code": "synthetic-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 302 and "sso_error=1" in callback.headers["location"]
    assert "#access_token=" not in callback.headers["location"]
    assert await db_session.scalar(select(func.count()).select_from(User)) == before_users
    assert await db_session.scalar(select(func.count()).select_from(Session)) == before_sessions
    db_session.expire_all()
    attempt = await db_session.scalar(select(SsoLoginAttempt))
    assert attempt.consumed_at is not None
    assert all(req.method == "GET" for req in seen)
    assert not auth_client.cookies.get(oidc.sso_transactions.cookie_name(state))
    payload["value"] = metadata()
    restarted = await auth_client.get("/api/v1/auth/sso/acme/authorize", follow_redirects=False)
    assert restarted.status_code == 302 and state not in restarted.headers["location"]


@pytest.mark.asyncio
async def test_public_multihost_provider_completes_real_network_seams(
    auth_client,
    db_session,
    monkeypatch,
    wire,
):
    from app.models.organization import Organization

    seen, _ = wire
    private, jwks = _keypair()
    org_id = await db_session.scalar(select(Organization.id))
    conn = await _connection(db_session, org_id, issuer=ISSUER)

    async def provider(self, req):
        seen.append(req)
        await req.aread()
        data = metadata()
        if req.headers["host"].startswith("tokens."):
            data = {"id_token": _mint(private, iss=ISSUER)}
        elif req.headers["host"].startswith("keys."):
            data = jwks
        return httpx.Response(200, json=data, request=req)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", provider)
    user, org = await oidc.finish_login(
        db_session,
        conn,
        code="synthetic-code",
        nonce="nonce-123",
        code_verifier="synthetic-verifier",
    )
    assert user.email == "jane@corp.example" and org.id == org_id
    assert len(seen) == 3 and all(req.url.host == PUBLIC_IP for req in seen)
    assert [req.extensions["sni_hostname"] for req in seen] == [
        "idp.example.test",
        "tokens.example.test",
        "keys.example.test",
    ]


@pytest.mark.asyncio
async def test_request_deadline_cancels_slow_http(monkeypatch, wire):
    import asyncio

    canceled = []

    async def stalled(self, req):
        try:
            await asyncio.Event().wait()
        finally:
            canceled.append(True)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", stalled)
    monkeypatch.setattr(oidc, "_IDP_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(oidc.SsoError):
        await asyncio.wait_for(request("token", metadata()["token_endpoint"]), timeout=1)
    assert canceled == [True]


@pytest.mark.asyncio
async def test_bad_json_is_sanitized(monkeypatch, wire):
    async def invalid(self, req):
        return httpx.Response(200, content=b"synthetic-client-secret is not JSON", request=req)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", invalid)
    with pytest.raises(oidc.SsoError) as failure:
        await request("token", metadata()["token_endpoint"])
    assert "synthetic-client-secret" not in str(failure.value)


@pytest.mark.asyncio
async def test_issuer_query_is_refused_before_network(wire):
    seen, _ = wire
    with pytest.raises(oidc.SsoError):
        await oidc.discover(ISSUER + "?other=issuer")
    assert seen == []


@pytest.mark.asyncio
async def test_deadline_covers_off_loop_dns_without_late_transmission(monkeypatch, wire):
    import asyncio
    import threading

    seen, _ = wire
    loop_thread = threading.get_ident()
    resolver_threads = []
    release, finished = threading.Event(), threading.Event()

    def slow_dns(*args):
        resolver_threads.append(threading.get_ident())
        try:
            release.wait(timeout=2)
            return [PUBLIC_IP]
        finally:
            finished.set()

    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", slow_dns)
    monkeypatch.setattr(oidc, "_IDP_TIMEOUT_SECONDS", 0.2)
    try:
        with pytest.raises(oidc.SsoError):
            await asyncio.wait_for(request("token", metadata()["token_endpoint"]), timeout=1)
    finally:
        release.set()
    assert resolver_threads and resolver_threads[0] != loop_thread
    assert await asyncio.to_thread(finished.wait, 1)
    assert seen == []
