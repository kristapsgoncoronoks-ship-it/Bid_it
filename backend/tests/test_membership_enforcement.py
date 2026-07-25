"""Active-membership enforcement + membership-based counts (Slice 6d).

deps now requires a LIVE membership in the active org (a suspended membership is a
hard 401, even if the global account is active), and owner/approver/seat counts
come from memberships (so a member active in another org still counts here).
"""

import pytest
from sqlalchemy import select, update

from app.models.membership import Membership
from app.models.user import User


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


async def _register(client, org, email):
    r = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "name": "P", "email": email, "password": "supersecret"},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"]


@pytest.mark.asyncio
async def test_suspended_membership_blocks_access(client, db_session):
    tok = await _register(client, "Acme", "owner@acme.io")
    assert (await client.get("/api/v1/auth/me", headers=_h(tok))).status_code == 200

    # Suspend the membership for the user's active org.
    user = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    await db_session.execute(
        update(Membership).where(Membership.user_id == user.id).values(status="suspended")
    )
    await db_session.commit()

    # The still-valid token is now rejected — membership, not just the account,
    # gates access.
    assert (await client.get("/api/v1/auth/me", headers=_h(tok))).status_code == 401


@pytest.mark.asyncio
async def test_dual_write_keeps_membership_in_sync(client, db_session):
    owner = await _register(client, "Acme", "owner@acme.io")
    # Invite + accept a second member.
    invite = (
        await client.post(
            "/api/v1/team/invites",
            json={"email": "staff@acme.io", "role": "user"},
            headers=_h(owner),
        )
    ).json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "Staff", "password": "supersecret"},
    )
    staff_id = acc.json()["user"]["id"]

    # Promote staff to admin via member management.
    r = await client.patch(
        f"/api/v1/team/members/{staff_id}", json={"role": "admin"}, headers=_h(owner)
    )
    assert r.status_code == 200

    # The membership row reflects the change (source of truth for this org).
    m = await db_session.scalar(select(Membership).where(Membership.user_id == staff_id))
    assert m.role.value == "admin"


async def _owner_plus_staff(client) -> tuple[str, str, str]:
    """Register an owner, invite + accept a staff member. Returns
    (owner_token, staff_token, staff_id)."""
    owner = await _register(client, "Acme", "owner@acme.io")
    invite = (
        await client.post(
            "/api/v1/team/invites",
            json={"email": "staff@acme.io", "role": "user"},
            headers=_h(owner),
        )
    ).json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "Staff", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    return owner, acc.json()["token"]["access_token"], acc.json()["user"]["id"]


@pytest.mark.asyncio
async def test_role_change_revokes_sessions(client):
    """WO-4: a role change forces re-auth — the old token must not keep the old
    role's permissions for up to the token TTL."""
    owner, staff_tok, staff_id = await _owner_plus_staff(client)
    assert (await client.get("/api/v1/auth/me", headers=_h(staff_tok))).status_code == 200

    r = await client.patch(
        f"/api/v1/team/members/{staff_id}", json={"role": "admin"}, headers=_h(owner)
    )
    assert r.status_code == 200

    # The old token is dead immediately…
    assert (await client.get("/api/v1/auth/me", headers=_h(staff_tok))).status_code == 401
    # …and a fresh login carries the NEW role.
    login = await client.post(
        "/api/v1/auth/login", json={"email": "staff@acme.io", "password": "supersecret"}
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "admin"


@pytest.mark.asyncio
async def test_user_deactivation_revokes_sessions(client, db_session):
    from app.models.session import Session as SessionModel

    owner, staff_tok, staff_id = await _owner_plus_staff(client)
    r = await client.patch(
        f"/api/v1/team/members/{staff_id}", json={"is_active": False}, headers=_h(owner)
    )
    assert r.status_code == 200

    assert (await client.get("/api/v1/auth/me", headers=_h(staff_tok))).status_code == 401
    # Not merely gated by membership status — the session rows are revoked.
    live = list(
        await db_session.scalars(
            select(SessionModel).where(
                SessionModel.user_id == staff_id, SessionModel.revoked_at.is_(None)
            )
        )
    )
    assert live == []


@pytest.mark.asyncio
async def test_revocation_is_audited_with_trigger_and_count(client, db_session):
    import json

    from app.models.audit import AuditEvent

    owner, _staff_tok, staff_id = await _owner_plus_staff(client)
    r = await client.patch(
        f"/api/v1/team/members/{staff_id}", json={"role": "admin"}, headers=_h(owner)
    )
    assert r.status_code == 200

    row = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "session.revoked_bulk")
    )
    assert row is not None and row.target_id == staff_id
    meta = json.loads(row.meta)
    assert meta["trigger"] == "role_change"
    assert meta["count"] == 1  # exactly the staff member's one live session


@pytest.mark.asyncio
async def test_seat_count_is_membership_based(client, db_session):
    # One owner = one seat. Add a member = two seats. Counts come from memberships.
    from app.services import plans

    owner = await _register(client, "Acme", "owner@acme.io")
    user = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    assert await plans.active_seats(db_session, user.org_id) == 1

    invite = (
        await client.post(
            "/api/v1/team/invites", json={"email": "s@acme.io", "role": "user"}, headers=_h(owner)
        )
    ).json()["token"]
    await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "S", "password": "supersecret"},
    )
    assert await plans.active_seats(db_session, user.org_id) == 2
