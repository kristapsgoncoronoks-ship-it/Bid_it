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


# --- WO-AE: one vocabulary, the four business roles, a stated tie-break ------

BUSINESS_ROLES = ("finance_manager", "accountant", "approver", "auditor")


def test_wo_ae_the_four_business_roles_are_assignable_via_a_group():
    """The gap WO-AE closes. Before it, each of these returned None: the group
    matched, the role name parsed, and `_ASSIGNABLE` — a three-member tuple
    written before A1.5 — dropped it on the floor while the admin who saved the
    mapping saw no error."""
    for role in BUSINESS_ROLES:
        assert oidc.role_from_groups(json.dumps({"Team": role}), ["Team"]) == role, role


def test_wo_ae_owner_is_still_never_assignable_via_idp():
    """The exclusion that has to survive the widening: `owner` is the founder's
    role and an external IdP never grants it, mapped or not."""
    assert oidc.role_from_groups(json.dumps({"Founders": "owner"}), ["Founders"]) is None
    assert UserRole.owner not in oidc._ASSIGNABLE
    assert "owner" not in oidc.assignable_role_values()


def test_wo_ae_the_two_role_vocabularies_cannot_drift():
    """Structural. `roles.ASSIGNABLE_ROLES` is the one list; the IdP set is that
    list minus owner, DERIVED from it. A role added to one and not the other
    fails here — which is exactly how the four business roles went missing from
    SSO."""
    from app.core.roles import ASSIGNABLE_ROLES, IDP_ASSIGNABLE_ROLES

    assert set(oidc._ASSIGNABLE) == set(ASSIGNABLE_ROLES) - {UserRole.owner}
    assert oidc._ASSIGNABLE is IDP_ASSIGNABLE_ROLES
    assert tuple(oidc.assignable_role_values()) == tuple(r.value for r in IDP_ASSIGNABLE_ROLES)
    # Every member of the vocabulary is a real stored role.
    assert set(oidc.assignable_role_values()) <= set(UserRole.__members__)


def test_wo_ae_a_tie_between_equal_rank_roles_is_deterministic():
    """`ROLE_RANK` puts the four business roles level with `user`. "Highest
    wins" is therefore a tie, and before WO-AE the winner was whichever key the
    JSON happened to list first. The rule now: later in `ASSIGNABLE_ROLES`
    declaration order wins — a business role beats plain `user`, and the
    business roles order as `authz.Role` declares them. Both orderings of the
    same mapping must agree."""
    a = json.dumps({"Ops": "accountant", "Approvers": "approver"})
    b = json.dumps({"Approvers": "approver", "Ops": "accountant"})
    groups = ["Ops", "Approvers"]
    assert oidc.role_from_groups(a, groups) == oidc.role_from_groups(b, groups) == "approver"

    # A business role beats plain `user` at the same rank — in either order.
    m = json.dumps({"Everyone": "user", "Finance": "finance_manager"})
    assert oidc.role_from_groups(m, ["Everyone", "Finance"]) == "finance_manager"
    m_rev = json.dumps({"Finance": "finance_manager", "Everyone": "user"})
    assert oidc.role_from_groups(m_rev, ["Everyone", "Finance"]) == "finance_manager"

    # But rank still comes first: admin outranks every business role.
    m2 = json.dumps({"Finance": "finance_manager", "IT": "admin"})
    assert oidc.role_from_groups(m2, ["Finance", "IT"]) == "admin"


@pytest.mark.asyncio
async def test_wo_ae_jit_provisions_a_business_role_from_a_group(auth_client, db_session, _patch):
    org_id = await db_session.scalar(select(Organization.id))
    _patch(email="auditor-new@corp.example", groups=["Auditors"])
    conn = await _conn(
        db_session, org_id, jit_enabled=True, role_mappings=json.dumps({"Auditors": "auditor"})
    )
    user, _ = await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")
    assert user.role == UserRole.auditor


@pytest.mark.asyncio
async def test_wo_ae_a_stored_owner_default_never_provisions_an_owner(
    auth_client, db_session, _patch
):
    """The schema refuses `default_role="owner"` now; a row written before it
    could still carry one. The JIT path holds the promise regardless: it falls
    back to `user`, never to `owner`."""
    org_id = await db_session.scalar(select(Organization.id))
    _patch(email="nobody-special@corp.example", groups=[])
    conn = await _conn(db_session, org_id, default_role="owner")
    user, _ = await oidc.finish_login(db_session, conn, code="c", nonce="n1", code_verifier="v")
    assert user.role == UserRole.user


@pytest.mark.asyncio
async def test_wo_ae_scim_default_holds_the_same_owner_exclusion(auth_client, db_session):
    """SCIM is an identity provider too. Its provisioning default falls back to
    `user` for anything outside the IdP vocabulary — `owner` included."""
    from app.services import scim

    org_id = await db_session.scalar(select(Organization.id))
    owner_default = await scim.create_user(
        db_session, org_id, {"userName": "scim-a@corp.example"}, default_role="owner"
    )
    business_default = await scim.create_user(
        db_session, org_id, {"userName": "scim-b@corp.example"}, default_role="accountant"
    )
    assert owner_default.role == UserRole.user
    assert business_default.role == UserRole.accountant


# --- the route: validators + served vocabulary --------------------------------


@pytest.mark.asyncio
async def test_wo_ae_default_role_accepts_a_business_role_and_refuses_a_non_role(auth_client):
    ok = await auth_client.put(
        "/api/v1/sso/connection",
        json={"slug": "acme", "issuer": ISSUER, "client_id": CLIENT_ID, "default_role": "auditor"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["default_role"] == "auditor"

    # "processor" is what the settings screen used to OFFER. It is not a role;
    # the old regex refused it too, but with a pattern string — and the screen
    # never learned. The refusal names the vocabulary now, and the vocabulary
    # it names does not include owner.
    bad = await auth_client.put(
        "/api/v1/sso/connection",
        json={
            "slug": "acme",
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "default_role": "processor",
        },
    )
    assert bad.status_code == 422, bad.text
    sentence = bad.json()["detail"][0]["msg"]
    assert "processor" in sentence and "auditor" in sentence
    assert "owner" not in sentence.split("must be one of")[-1]

    # And owner itself is refused as a default, by the same sentence.
    owner = await auth_client.put(
        "/api/v1/sso/connection",
        json={"slug": "acme", "issuer": ISSUER, "client_id": CLIENT_ID, "default_role": "owner"},
    )
    assert owner.status_code == 422, owner.text


@pytest.mark.asyncio
async def test_wo_ae_a_mapping_to_a_role_the_login_path_would_drop_is_refused_at_save(auth_client):
    """Before WO-AE a mapping to `owner` or to a typo was ACCEPTED and then
    ignored at login — the admin was told it worked. It is refused now, and the
    refusal names the group so a ten-row mapping can be fixed without guessing."""
    for role in ("owner", "processor", "superuser"):
        r = await auth_client.put(
            "/api/v1/sso/connection",
            json={
                "slug": "acme",
                "issuer": ISSUER,
                "client_id": CLIENT_ID,
                "role_mappings": {"Finance": "finance_manager", "Founders": role},
            },
        )
        assert r.status_code == 422, (role, r.text)
        assert "Founders" in r.json()["detail"][0]["msg"]

    # Every member of the vocabulary saves.
    r = await auth_client.put(
        "/api/v1/sso/connection",
        json={
            "slug": "acme",
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "role_mappings": {f"G-{role}": role for role in oidc.assignable_role_values()},
        },
    )
    assert r.status_code == 200, r.text
    assert set(r.json()["role_mappings"].values()) == set(oidc.assignable_role_values())


@pytest.mark.asyncio
async def test_wo_ae_the_server_serves_the_assignable_vocabulary(auth_client):
    """Both places the SPA reads it from: on the connection, and standalone
    before the first save (when `GET /connection` is null)."""
    standalone = await auth_client.get("/api/v1/sso/assignable-roles")
    assert standalone.status_code == 200, standalone.text
    assert standalone.json() == list(oidc.assignable_role_values())

    r = await auth_client.put(
        "/api/v1/sso/connection",
        json={"slug": "acme", "issuer": ISSUER, "client_id": CLIENT_ID},
    )
    assert r.status_code == 200, r.text
    served = r.json()["assignable_roles"]
    assert served == standalone.json()
    assert set(BUSINESS_ROLES) <= set(served)
    assert "owner" not in served
    assert "processor" not in served
