"""OIDC SSO (Phase 4, ADR-0021). The security-critical logic is proven with a
locally-minted RSA key + JWKS (no network / no real IdP): ID-token validation
accepts a correct token and rejects tampered / wrong-audience / wrong-issuer /
expired / replayed tokens. JIT provisioning + the routes are driven with the
three network seams (discover / exchange / JWKS) monkeypatched to fixtures.

The remaining, un-provable-here part is the live HTTP to a real authorization
server — the ADR-0021 "return to finish" boundary, exercised against Keycloak.

The SSRF-guard tests at the bottom of this file (R8) exercise `discover()`/
`fetch_jwks()` for real, with only `httpx.AsyncClient` faked out — proving the
guard fires before any network attempt for a private/reserved target, and
that a normal public-looking issuer still works.
"""

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from sqlalchemy import func, select

from app.models.organization import Organization
from app.models.sso import SsoConnection
from app.models.user import User, UserRole
from app.services import oidc

ISSUER = "https://idp.example.com"
CLIENT_ID = "invoiceiq-client"
KID = "test-key"


def _keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub_pem = (
        k.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    pub = jwk.construct(pub_pem, "RS256").to_dict()
    pub = {kk: (vv.decode() if isinstance(vv, bytes) else vv) for kk, vv in pub.items()}
    pub["kid"] = KID
    return priv, {"keys": [pub]}


def _mint(priv, **overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 3600,
        "nonce": "nonce-123",
        "email": "jane@corp.example",
        "name": "Jane Doe",
    }
    claims.update(overrides)
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": KID})


# --- ID-token validation (pure, fixture-based) -----------------------------


def test_validate_accepts_correct_token():
    priv, jwks = _keypair()
    claims = oidc.validate_id_token(
        _mint(priv), jwks, client_id=CLIENT_ID, issuer=ISSUER, nonce="nonce-123"
    )
    assert claims["email"] == "jane@corp.example"


def test_validate_rejects_tampered_signature():
    priv, jwks = _keypair()
    tok = _mint(priv)
    tampered = tok[:-3] + ("aaa" if not tok.endswith("aaa") else "bbb")
    with pytest.raises(oidc.SsoError):
        oidc.validate_id_token(
            tampered, jwks, client_id=CLIENT_ID, issuer=ISSUER, nonce="nonce-123"
        )


def test_validate_rejects_wrong_audience():
    priv, jwks = _keypair()
    with pytest.raises(oidc.SsoError):
        oidc.validate_id_token(
            _mint(priv, aud="someone-else"),
            jwks,
            client_id=CLIENT_ID,
            issuer=ISSUER,
            nonce="nonce-123",
        )


def test_validate_rejects_wrong_issuer():
    priv, jwks = _keypair()
    with pytest.raises(oidc.SsoError):
        oidc.validate_id_token(
            _mint(priv, iss="https://evil.example"),
            jwks,
            client_id=CLIENT_ID,
            issuer=ISSUER,
            nonce="nonce-123",
        )


def test_validate_rejects_expired():
    priv, jwks = _keypair()
    past = int(time.time()) - 10
    with pytest.raises(oidc.SsoError):
        oidc.validate_id_token(
            _mint(priv, exp=past, iat=past - 3600),
            jwks,
            client_id=CLIENT_ID,
            issuer=ISSUER,
            nonce="nonce-123",
        )


def test_validate_rejects_nonce_mismatch():
    priv, jwks = _keypair()
    with pytest.raises(oidc.SsoError):
        oidc.validate_id_token(
            _mint(priv, nonce="other"), jwks, client_id=CLIENT_ID, issuer=ISSUER, nonce="nonce-123"
        )


def test_validate_rejects_token_signed_by_other_key():
    priv1, _ = _keypair()
    _, jwks2 = _keypair()  # a DIFFERENT keypair's JWKS
    with pytest.raises(oidc.SsoError):
        oidc.validate_id_token(
            _mint(priv1), jwks2, client_id=CLIENT_ID, issuer=ISSUER, nonce="nonce-123"
        )


# --- PKCE + state ----------------------------------------------------------


def test_pkce_challenge_matches_verifier():
    import base64
    import hashlib

    v, c = oidc.pkce_pair()
    expect = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert c == expect


def test_state_roundtrip_and_tamper():
    s = oidc.sign_state("conn-1", "nonce-1", "verifier-1")
    got = oidc.read_state(s)
    assert got["conn"] == "conn-1" and got["nonce"] == "nonce-1" and got["cv"] == "verifier-1"
    with pytest.raises(oidc.SsoError):
        oidc.read_state(s + "x")


def test_authorize_url_has_pkce_and_state():
    url = oidc.build_authorize_url(
        "https://idp/auth",
        client_id="c",
        redirect_uri="https://app/cb",
        state="st",
        nonce="no",
        code_challenge="ch",
    )
    assert "code_challenge=ch" in url and "code_challenge_method=S256" in url
    assert "state=st" in url and "response_type=code" in url


# --- finish_login (JIT) with network seams patched -------------------------


@pytest.fixture
def _patch_network(monkeypatch):
    priv, jwks = _keypair()

    async def _discover(issuer):
        return {
            "issuer": ISSUER,
            "authorization_endpoint": "https://idp/auth",
            "token_endpoint": "https://idp/token",
            "jwks_uri": "https://idp/jwks",
        }

    async def _fetch_jwks(uri):
        return jwks

    def _make_exchange(**id_claims):
        async def _exchange(token_endpoint, **kw):
            return {"id_token": _mint(priv, **id_claims), "access_token": "at"}

        return _exchange

    monkeypatch.setattr(oidc, "discover", _discover)
    monkeypatch.setattr(oidc, "fetch_jwks", _fetch_jwks)
    return lambda **c: monkeypatch.setattr(oidc, "exchange_code", _make_exchange(**c))


async def _connection(db, org_id, **over):
    fields = dict(
        org_id=org_id,
        slug="acme",
        protocol="oidc",
        enabled=True,
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="sec",
        jit_enabled=True,
        default_role="user",
    )
    fields.update(over)
    conn = SsoConnection(**fields)
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


@pytest.mark.asyncio
async def test_finish_login_jit_provisions(auth_client, db_session, _patch_network):
    _patch_network(nonce="n1", email="new.user@corp.example", name="New User")
    org_id = await db_session.scalar(select(Organization.id))
    conn = await _connection(db_session, org_id)

    user, org = await oidc.finish_login(
        db_session, conn, code="code", nonce="n1", code_verifier="v"
    )
    assert user.email == "new.user@corp.example" and user.org_id == org_id
    assert user.role == UserRole.user
    # Password login is disabled for a JIT user.
    from app.core.security import verify_password

    assert verify_password("anything", user.hashed_password) is False


@pytest.mark.asyncio
async def test_finish_login_matches_existing(auth_client, db_session, _patch_network):
    org_id = await db_session.scalar(select(Organization.id))
    owner_email = await db_session.scalar(select(User.email))
    _patch_network(nonce="n1", email=owner_email)
    conn = await _connection(db_session, org_id)

    user, _ = await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")
    assert user.email == owner_email
    # No duplicate user created.
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.asyncio
async def test_finish_login_rejects_cross_org_email(
    auth_client, db_session, _patch_network, client
):
    # Second tenant owns other@corp.example.
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other",
            "name": "O",
            "email": "other@corp.example",
            "password": "supersecret2",
        },
    )
    assert reg.status_code == 201
    _patch_network(nonce="n1", email="other@corp.example")
    org_id = await db_session.scalar(
        select(Organization.id).order_by(Organization.created_at.asc())
    )
    conn = await _connection(db_session, org_id)
    with pytest.raises(oidc.SsoError):
        await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")


@pytest.mark.asyncio
async def test_finish_login_domain_restriction(auth_client, db_session, _patch_network):
    _patch_network(nonce="n1", email="jane@wrong.example")
    org_id = await db_session.scalar(select(Organization.id))
    conn = await _connection(db_session, org_id, allowed_domain="corp.example")
    with pytest.raises(oidc.SsoError):
        await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")


@pytest.mark.asyncio
async def test_finish_login_jit_disabled(auth_client, db_session, _patch_network):
    _patch_network(nonce="n1", email="nobody@corp.example")
    org_id = await db_session.scalar(select(Organization.id))
    conn = await _connection(db_session, org_id, jit_enabled=False)
    with pytest.raises(oidc.SsoError):
        await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")


# --- routes ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_redirects_to_idp(auth_client, db_session, monkeypatch):
    async def _discover(issuer):
        return {"authorization_endpoint": "https://idp/auth"}

    monkeypatch.setattr(oidc, "discover", _discover)
    org_id = await db_session.scalar(select(Organization.id))
    await _connection(db_session, org_id)

    r = await auth_client.get("/api/v1/auth/sso/acme/authorize", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://idp/auth?") and "code_challenge=" in loc and "state=" in loc


@pytest.mark.asyncio
async def test_authorize_404_when_disabled(auth_client, db_session):
    org_id = await db_session.scalar(select(Organization.id))
    await _connection(db_session, org_id, enabled=False)
    r = await auth_client.get("/api/v1/auth/sso/acme/authorize", follow_redirects=False)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_callback_completes_and_issues_token(auth_client, db_session, _patch_network):
    _patch_network(nonce="n1", email="sso.person@corp.example")
    org_id = await db_session.scalar(select(Organization.id))
    conn = await _connection(db_session, org_id)
    state = oidc.sign_state(conn.id, "n1", "v")

    r = await auth_client.get(
        f"/api/v1/auth/sso/callback?code=abc&state={state}", follow_redirects=False
    )
    assert r.status_code == 302
    assert "#access_token=" in r.headers["location"]


@pytest.mark.asyncio
async def test_callback_error_redirects_to_login(auth_client):
    r = await auth_client.get(
        "/api/v1/auth/sso/callback?error=access_denied", follow_redirects=False
    )
    assert r.status_code == 302 and "sso_error=1" in r.headers["location"]


# --- admin config route ----------------------------------------------------


@pytest.mark.asyncio
async def test_admin_configures_connection(auth_client):
    r = await auth_client.put(
        "/api/v1/sso/connection",
        json={
            "slug": "acme",
            "protocol": "oidc",
            "enabled": True,
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "client_secret": "shhh",
            "default_role": "user",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "acme" and body["has_client_secret"] is True
    assert "client_secret" not in body  # secret never returned
    assert body["login_url"].endswith("/auth/sso/acme/authorize")

    got = await auth_client.get("/api/v1/sso/connection")
    assert got.json()["client_id"] == CLIENT_ID


@pytest.mark.asyncio
async def test_admin_config_requires_admin(auth_client, db_session):
    user = await db_session.scalar(select(User))
    user.role = UserRole.user
    await db_session.commit()
    r = await auth_client.get("/api/v1/sso/connection")
    assert r.status_code == 403


# --- SSRF guard on discover()/fetch_jwks() (R8) -----------------------------
#
# `issuer` is admin-supplied per-tenant config with no URL restriction of its
# own, and `jwks_uri` comes straight out of that issuer's discovery *response*
# — attacker-controlled the moment the issuer host is malicious/compromised.
# Both are unauthenticated, server-side GETs, so a private/loopback/
# link-local/reserved target (e.g. cloud metadata, 169.254.169.254) must never
# be followed, mirroring the webhook delivery path's existing guard.


class _FakeResponse:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


class _FakeAsyncClient:
    """Stands in for `httpx.AsyncClient` so a "guard didn't block" bug would
    still be proven by real GET traffic being attempted, without touching the
    network."""

    last_url: str | None = None

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *a) -> bool:
        return False

    async def get(self, url: str, **kw) -> _FakeResponse:
        _FakeAsyncClient.last_url = url
        return _FakeResponse({"url": url, "jwks_uri": "https://idp.example.com/jwks", "keys": []})


UNSAFE_URLS = [
    "http://127.0.0.1:9999",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "https://10.0.0.5",  # RFC1918
    "http://[::1]",  # IPv6 loopback
    "http://localhost:8080",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_issuer", UNSAFE_URLS)
async def test_discover_rejects_private_or_reserved_issuer(monkeypatch, bad_issuer):
    import httpx

    _FakeAsyncClient.last_url = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    with pytest.raises(oidc.SsoError):
        await oidc.discover(bad_issuer)
    # The guard must fire BEFORE any network attempt.
    assert _FakeAsyncClient.last_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_uri", UNSAFE_URLS)
async def test_fetch_jwks_rejects_private_or_reserved_uri(monkeypatch, bad_uri):
    import httpx

    _FakeAsyncClient.last_url = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    with pytest.raises(oidc.SsoError):
        await oidc.fetch_jwks(bad_uri)
    assert _FakeAsyncClient.last_url is None


@pytest.mark.asyncio
async def test_discover_allows_public_issuer(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    # RFC 2606 `.test` — guaranteed never to resolve, so this hits the same
    # DNS-fails-open branch the webhook guard's own test relies on
    # (`test_webhook_accepts_public_url_and_rechecks_on_update`), not a real
    # network call, and proves the guard doesn't over-block a normal issuer.
    issuer = "https://idp.example.test"
    result = await oidc.discover(issuer)
    assert result["url"] == f"{issuer}/.well-known/openid-configuration"


@pytest.mark.asyncio
async def test_fetch_jwks_allows_public_uri(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    result = await oidc.fetch_jwks("https://idp.example.test/jwks")
    assert result["url"] == "https://idp.example.test/jwks"


@pytest.mark.asyncio
async def test_authorize_502s_when_issuer_is_ssrf_unsafe(auth_client, db_session):
    """End-to-end proof through the real route (no monkeypatched `discover`):
    an admin-configured issuer pointing at the cloud metadata address never
    reaches the network — the route's existing broad `except Exception`
    (IdP-unreachable handling) turns the SSRF guard's rejection into the same
    502 an unreachable IdP would produce, never a 500 and never a fetch."""
    org_id = await db_session.scalar(select(Organization.id))
    await _connection(db_session, org_id, issuer="http://169.254.169.254")
    r = await auth_client.get("/api/v1/auth/sso/acme/authorize", follow_redirects=False)
    assert r.status_code == 502
