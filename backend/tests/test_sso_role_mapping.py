"""IdP group → role mapping for SSO (Phase 4, ADR-0021): highest-role wins,
owner is never granted/demoted, JIT uses the mapped role, and role-sync updates
an existing user only when enabled."""

import json

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.models.sso import SsoConnection
from app.models.user import User, UserRole
from app.services import oidc

ISSUER = "https://idp.example.com"
CLIENT_ID = "client"


# --- pure helper -----------------------------------------------------------


def test_highest_mapped_role_wins():
    m = json.dumps({"Staff": "user", "Finance-Admins": "admin"})
    assert oidc.role_from_groups(m, ["Staff", "Finance-Admins"]) == "admin"
    assert oidc.role_from_groups(m, ["Staff"]) == "user"


def test_no_mapping_or_no_match_returns_none():
    assert oidc.role_from_groups(None, ["X"]) is None
    assert oidc.role_from_groups(json.dumps({"A": "admin"}), []) is None
    assert oidc.role_from_groups(json.dumps({"A": "admin"}), ["B"]) is None


def test_owner_and_unknown_roles_ignored():
    assert oidc.role_from_groups(json.dumps({"X": "owner"}), ["X"]) is None
    assert oidc.role_from_groups(json.dumps({"X": "superuser"}), ["X"]) is None


def test_group_match_is_case_insensitive():
    assert oidc.role_from_groups(json.dumps({"Admins": "admin"}), ["admins"]) == "admin"


# --- finish_login integration (network seams patched) ----------------------


def _keypair_and_mint():
    import time

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jose import jwk, jwt

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub = jwk.construct(
        k.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
        "RS256",
    ).to_dict()
    pub = {kk: (vv.decode() if isinstance(vv, bytes) else vv) for kk, vv in pub.items()}
    pub["kid"] = "k"
    jwks = {"keys": [pub]}

    def mint(**over):
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 3600,
            "nonce": "n1",
            "email": "person@corp.example",
            "name": "Person",
        }
        claims.update(over)
        return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": "k"})

    return jwks, mint


@pytest.fixture
def _patch(monkeypatch):
    jwks, mint = _keypair_and_mint()

    async def _discover(_issuer):
        return {
            "issuer": ISSUER,
            "authorization_endpoint": "https://idp/auth",
            "token_endpoint": "https://idp/token",
            "jwks_uri": "https://idp/jwks",
        }

    async def _fetch_jwks(_uri):
        return jwks

    monkeypatch.setattr(oidc, "discover", _discover)
    monkeypatch.setattr(oidc, "fetch_jwks", _fetch_jwks)

    def set_claims(**c):
        async def _exchange(_ep, **kw):
            return {"id_token": mint(**c)}

        monkeypatch.setattr(oidc, "exchange_code", _exchange)

    return set_claims


async def _conn(db, org_id, **over):
    fields = dict(
        org_id=org_id,
        slug="acme",
        protocol="oidc",
        enabled=True,
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="s",
        jit_enabled=True,
        default_role="user",
        groups_claim="groups",
    )
    fields.update(over)
    c = SsoConnection(**fields)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


@pytest.mark.asyncio
async def test_jit_uses_mapped_role(auth_client, db_session, _patch):
    _patch(email="newadmin@corp.example", groups=["Finance-Admins"])
    org_id = await db_session.scalar(select(Organization.id))
    conn = await _conn(db_session, org_id, role_mappings=json.dumps({"Finance-Admins": "admin"}))

    user, _ = await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")
    assert user.email == "newadmin@corp.example" and user.role == UserRole.admin


@pytest.mark.asyncio
async def test_jit_falls_back_to_default_without_group(auth_client, db_session, _patch):
    _patch(email="plain@corp.example", groups=["Unmapped"])
    org_id = await db_session.scalar(select(Organization.id))
    conn = await _conn(
        db_session,
        org_id,
        default_role="user",
        role_mappings=json.dumps({"Finance-Admins": "admin"}),
    )
    user, _ = await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")
    assert user.role == UserRole.user


@pytest.mark.asyncio
async def test_role_sync_updates_existing_user(auth_client, db_session, _patch):
    org_id = await db_session.scalar(select(Organization.id))
    # Seed a plain 'user' who is in the admin group at the IdP.
    u = User(
        org_id=org_id,
        email="grow@corp.example",
        name="Grow",
        role=UserRole.user,
        hashed_password="x",
    )
    db_session.add(u)
    await db_session.commit()

    _patch(email="grow@corp.example", groups=["Admins"])
    conn = await _conn(
        db_session, org_id, role_sync=True, role_mappings=json.dumps({"Admins": "admin"})
    )
    user, _ = await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")
    assert user.role == UserRole.admin


@pytest.mark.asyncio
async def test_role_sync_off_leaves_existing_role(auth_client, db_session, _patch):
    org_id = await db_session.scalar(select(Organization.id))
    u = User(
        org_id=org_id,
        email="stay@corp.example",
        name="Stay",
        role=UserRole.user,
        hashed_password="x",
    )
    db_session.add(u)
    await db_session.commit()

    _patch(email="stay@corp.example", groups=["Admins"])
    conn = await _conn(
        db_session, org_id, role_sync=False, role_mappings=json.dumps({"Admins": "admin"})
    )
    user, _ = await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")
    assert user.role == UserRole.user  # unchanged


@pytest.mark.asyncio
async def test_role_sync_never_demotes_owner(auth_client, db_session, _patch):
    org_id = await db_session.scalar(select(Organization.id))
    owner_email = await db_session.scalar(select(User.email).where(User.role == UserRole.owner))

    _patch(email=owner_email, groups=["Staff"])
    conn = await _conn(
        db_session, org_id, role_sync=True, role_mappings=json.dumps({"Staff": "user"})
    )
    user, _ = await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")
    assert user.role == UserRole.owner  # owner protected


@pytest.mark.asyncio
async def test_admin_can_configure_mappings(auth_client):
    r = await auth_client.put(
        "/api/v1/sso/connection",
        json={
            "slug": "acme",
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "role_mappings": {"Finance-Admins": "admin", "Staff": "user"},
            "role_sync": True,
            "groups_claim": "groups",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["role_mappings"] == {"Finance-Admins": "admin", "Staff": "user"}
    assert body["role_sync"] is True
