"""Org status is a per-request gate + suspension revokes sessions (WO-4).

Suspending a tenant must bite on the very NEXT request from every member — not
when their 24h tokens happen to expire. The gate lives in `deps.get_current_user`
(401, code="organization_suspended"); the platform suspension lever additionally
revokes every live session pointed at the tenant, so reactivating the org never
resurrects old tokens. Platform-operator routes run unscoped and keep working
against a suspended tenant — that is how a suspension is reversed.
"""

import pytest
from sqlalchemy import event, select

from app.models.organization import Organization
from app.models.session import Session
from app.models.user import User


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client, org: str, email: str) -> str:
    r = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "name": "P", "email": email, "password": "supersecret"},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"]


async def _suspend_in_db(db_session, email: str) -> str:
    """Suspend the org of the user with this email directly in the DB (bypassing
    the platform endpoint — proves the per-request gate alone, no revocation)."""
    user = await db_session.scalar(select(User).where(User.email == email))
    org = await db_session.get(Organization, user.org_id)
    org.status = "suspended"
    await db_session.commit()
    return org.id


async def _make_operator(client, db_session) -> str:
    """A platform operator in their own (separate) org."""
    tok = await _register(client, "Platform Co", "op@platform.io")
    op = await db_session.scalar(select(User).where(User.email == "op@platform.io"))
    op.is_platform_admin = True
    await db_session.commit()
    return tok


@pytest.mark.asyncio
async def test_suspended_org_rejects_next_request(client, db_session):
    tok = await _register(client, "Acme", "owner@acme.io")
    assert (await client.get("/api/v1/auth/me", headers=_h(tok))).status_code == 200

    await _suspend_in_db(db_session, "owner@acme.io")

    # The very next request is refused — no 24h token-TTL grace.
    r = await client.get("/api/v1/auth/me", headers=_h(tok))
    assert r.status_code == 401
    assert r.json()["code"] == "organization_suspended"


@pytest.mark.asyncio
async def test_suspended_org_401_is_indistinguishable_from_invalid_token_except_by_code(
    client, db_session
):
    tok = await _register(client, "Acme", "owner@acme.io")
    await _suspend_in_db(db_session, "owner@acme.io")

    suspended = await client.get("/api/v1/auth/me", headers=_h(tok))
    invalid = await client.get("/api/v1/auth/me", headers=_h("not-a-real-token"))

    assert suspended.status_code == invalid.status_code == 401
    # Same human-readable body and auth-challenge header — org existence leaks
    # nowhere; only the additive machine `code` differs.
    assert suspended.json()["detail"] == invalid.json()["detail"]
    assert (
        suspended.headers.get("WWW-Authenticate")
        == invalid.headers.get("WWW-Authenticate")
        == "Bearer"
    )
    assert suspended.json()["code"] == "organization_suspended"
    assert "code" not in invalid.json()


@pytest.mark.asyncio
async def test_platform_operator_can_still_act_on_a_suspended_org(client, db_session):
    op_tok = await _make_operator(client, db_session)
    await _register(client, "Acme", "owner@acme.io")
    acme_id = await _suspend_in_db(db_session, "owner@acme.io")

    # The unscoped operator path still works against the suspended tenant…
    tenants = await client.get("/api/v1/platform/tenants", headers=_h(op_tok))
    assert tenants.status_code == 200
    assert any(t["id"] == acme_id and t["status"] == "suspended" for t in tenants.json())

    # …so suspension is reversible via the API.
    r = await client.patch(
        f"/api/v1/platform/tenants/{acme_id}", json={"status": "active"}, headers=_h(op_tok)
    )
    assert r.status_code == 200 and r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_suspension_revokes_all_member_sessions(client, db_session):
    op_tok = await _make_operator(client, db_session)
    owner_tok = await _register(client, "Acme", "owner@acme.io")
    # A second member, logged in with their own session.
    invite = (
        await client.post(
            "/api/v1/team/invites",
            json={"email": "staff@acme.io", "role": "user"},
            headers=_h(owner_tok),
        )
    ).json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "Staff", "password": "supersecret"},
    )
    staff_tok = acc.json()["token"]["access_token"]
    assert (await client.get("/api/v1/auth/me", headers=_h(owner_tok))).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=_h(staff_tok))).status_code == 200

    owner = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    acme_id = owner.org_id
    r = await client.patch(
        f"/api/v1/platform/tenants/{acme_id}", json={"status": "suspended"}, headers=_h(op_tok)
    )
    assert r.status_code == 200

    # BOTH members' tokens are dead on the next request.
    assert (await client.get("/api/v1/auth/me", headers=_h(owner_tok))).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=_h(staff_tok))).status_code == 401
    # And not merely gated: the session rows themselves are revoked.
    live = list(
        await db_session.scalars(
            select(Session).where(Session.org_id == acme_id, Session.revoked_at.is_(None))
        )
    )
    assert live == []


@pytest.mark.asyncio
async def test_reactivating_an_org_requires_new_login(client, db_session):
    op_tok = await _make_operator(client, db_session)
    owner_tok = await _register(client, "Acme", "owner@acme.io")
    owner = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    acme_id = owner.org_id

    for status_ in ("suspended", "active"):
        r = await client.patch(
            f"/api/v1/platform/tenants/{acme_id}", json={"status": status_}, headers=_h(op_tok)
        )
        assert r.status_code == 200

    # Revocation is one-way: the pre-suspension token stays dead after reactivation…
    assert (await client.get("/api/v1/auth/me", headers=_h(owner_tok))).status_code == 401
    # …and a fresh login works again.
    login = await client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.io", "password": "supersecret"}
    )
    assert login.status_code == 200
    fresh = login.json()["token"]["access_token"]
    assert (await client.get("/api/v1/auth/me", headers=_h(fresh))).status_code == 200


@pytest.mark.asyncio
async def test_request_does_not_add_a_second_org_query(client, db_session):
    """Performance guard (WO-4): the org-status gate reuses the request's ONE
    organizations fetch — `/auth/me` resolves both CurrentUser and CurrentOrg,
    and must still issue exactly one SELECT against organizations."""
    tok = await _register(client, "Acme", "owner@acme.io")

    bind = db_session.bind
    sync_engine = getattr(bind, "sync_engine", bind)
    org_selects: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        s = statement.lower()
        if s.lstrip().startswith("select") and "from organizations" in s:
            org_selects.append(statement)

    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        r = await client.get("/api/v1/auth/me", headers=_h(tok))
        assert r.status_code == 200
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    assert len(org_selects) == 1, org_selects
