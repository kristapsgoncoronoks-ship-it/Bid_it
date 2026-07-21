"""SCIM 2.0 provisioning (Phase 4, ADR-0021): token auth, create/list/get/
replace/patch(deactivate)/delete over Users, tenant scoping — all offline."""
import pytest
from sqlalchemy import func, select

from app.models.organization import Organization
from app.models.sso import SsoConnection
from app.models.user import User
from app.services import scim


async def _conn(db, org_id, **over):
    fields = dict(org_id=org_id, slug="acme", protocol="oidc", enabled=True,
                  default_role="user", scim_enabled=False)
    fields.update(over)
    c = SsoConnection(**fields)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _enable_scim(db, org_id) -> str:
    token = await scim.set_token(db, org_id)
    return token


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


async def _org_id(db):
    return await db.scalar(select(Organization.id))


# --- token -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_required(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _conn(db_session, org_id)
    await _enable_scim(db_session, org_id)
    r = await auth_client.get("/api/v1/scim/v2/Users")     # no bearer
    assert r.status_code == 401
    assert r.json()["schemas"][0].endswith(":Error")


@pytest.mark.asyncio
async def test_token_is_hashed_not_stored_plaintext(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _conn(db_session, org_id)
    token = await _enable_scim(db_session, org_id)
    conn = await db_session.scalar(select(SsoConnection))
    await db_session.refresh(conn)
    assert conn.scim_token_hash == scim.hash_token(token)
    assert token not in (conn.scim_token_hash or "")


# --- CRUD ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_list_get_user(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _conn(db_session, org_id)
    token = await _enable_scim(db_session, org_id)

    c = await auth_client.post("/api/v1/scim/v2/Users", headers=_hdr(token), json={
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "alice@corp.example",
        "name": {"givenName": "Alice", "familyName": "Smith"},
        "active": True,
    })
    assert c.status_code == 201, c.text
    uid = c.json()["id"]
    assert c.json()["userName"] == "alice@corp.example"
    assert c.json()["active"] is True

    lst = await auth_client.get('/api/v1/scim/v2/Users?filter=userName eq "alice@corp.example"',
                                headers=_hdr(token))
    body = lst.json()
    assert body["totalResults"] == 1 and body["Resources"][0]["id"] == uid

    g = await auth_client.get(f"/api/v1/scim/v2/Users/{uid}", headers=_hdr(token))
    assert g.status_code == 200 and g.json()["name"]["formatted"] == "Alice Smith"


@pytest.mark.asyncio
async def test_patch_deactivates_user(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _conn(db_session, org_id)
    token = await _enable_scim(db_session, org_id)
    c = await auth_client.post("/api/v1/scim/v2/Users", headers=_hdr(token),
                               json={"userName": "bob@corp.example"})
    uid = c.json()["id"]

    p = await auth_client.patch(f"/api/v1/scim/v2/Users/{uid}", headers=_hdr(token), json={
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    })
    assert p.status_code == 200 and p.json()["active"] is False
    user = await db_session.scalar(select(User).where(User.id == uid))
    assert user.is_active is False


@pytest.mark.asyncio
async def test_delete_soft_deactivates(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _conn(db_session, org_id)
    token = await _enable_scim(db_session, org_id)
    c = await auth_client.post("/api/v1/scim/v2/Users", headers=_hdr(token),
                               json={"userName": "carol@corp.example"})
    uid = c.json()["id"]

    d = await auth_client.delete(f"/api/v1/scim/v2/Users/{uid}", headers=_hdr(token))
    assert d.status_code == 204
    # Soft delete: row remains, deactivated (audit/FK integrity preserved).
    user = await db_session.scalar(select(User).where(User.id == uid))
    assert user is not None and user.is_active is False


@pytest.mark.asyncio
async def test_replace_user(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _conn(db_session, org_id)
    token = await _enable_scim(db_session, org_id)
    c = await auth_client.post("/api/v1/scim/v2/Users", headers=_hdr(token),
                               json={"userName": "dave@corp.example", "name": {"formatted": "Dave"}})
    uid = c.json()["id"]
    r = await auth_client.put(f"/api/v1/scim/v2/Users/{uid}", headers=_hdr(token),
                              json={"userName": "dave@corp.example", "name": {"formatted": "David Jones"},
                                    "active": True})
    assert r.status_code == 200 and r.json()["name"]["formatted"] == "David Jones"


@pytest.mark.asyncio
async def test_missing_username_is_400(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _conn(db_session, org_id)
    token = await _enable_scim(db_session, org_id)
    r = await auth_client.post("/api/v1/scim/v2/Users", headers=_hdr(token), json={"name": {"formatted": "X"}})
    assert r.status_code == 400


# --- tenant scoping --------------------------------------------------------

@pytest.mark.asyncio
async def test_scim_is_tenant_scoped(auth_client, db_session, client):
    org_id = await _org_id(db_session)
    await _conn(db_session, org_id)
    token = await _enable_scim(db_session, org_id)
    c = await auth_client.post("/api/v1/scim/v2/Users", headers=_hdr(token),
                               json={"userName": "acme.user@corp.example"})
    uid = c.json()["id"]

    # A second org with its own SCIM token cannot see/fetch the first org's user.
    reg = await client.post("/api/v1/auth/register", json={
        "organization_name": "Other", "name": "O", "email": "o@o.io", "password": "supersecret2",
    })
    other_org = reg.json()["organization"]["id"]
    c2 = SsoConnection(org_id=other_org, slug="other", protocol="oidc", enabled=True,
                       default_role="user")
    db_session.add(c2)
    await db_session.commit()
    token2 = await scim.set_token(db_session, other_org)

    g = await auth_client.get(f"/api/v1/scim/v2/Users/{uid}", headers=_hdr(token2))
    assert g.status_code == 404      # not in token2's org

    lst = await auth_client.get("/api/v1/scim/v2/Users", headers=_hdr(token2))
    ids = [u["id"] for u in lst.json()["Resources"]]
    assert uid not in ids


# --- admin token endpoint --------------------------------------------------

@pytest.mark.asyncio
async def test_admin_generates_scim_token(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _conn(db_session, org_id)
    r = await auth_client.post("/api/v1/sso/scim/token")
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.startswith("scim_")
    # The freshly minted token authenticates SCIM calls.
    lst = await auth_client.get("/api/v1/scim/v2/Users", headers=_hdr(token))
    assert lst.status_code == 200


@pytest.mark.asyncio
async def test_scim_token_requires_connection(auth_client):
    r = await auth_client.post("/api/v1/sso/scim/token")   # no connection configured
    assert r.status_code == 400
