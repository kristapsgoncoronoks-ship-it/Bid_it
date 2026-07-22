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
