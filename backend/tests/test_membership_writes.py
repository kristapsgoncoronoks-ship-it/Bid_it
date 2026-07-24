"""Membership dual-write + multi-org join (Slice 6b).

Every user-creation path now writes a membership, and an existing account can be
invited into a SECOND org (password-verified) — the first user-visible multi-org
capability. Still dual-read: switching to the new org lands in a later slice.
"""

import pytest
from sqlalchemy import func, select

from app.models.membership import Membership
from app.models.user import User


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


async def _register(client, org, email):
    r = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "name": "Owner", "email": email, "password": "supersecret"},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"], r.json()["organization"]["id"]


async def _memberships_of(db, email) -> list[Membership]:
    user = await db.scalar(select(User).where(User.email == email))
    rows = await db.scalars(select(Membership).where(Membership.user_id == user.id))
    return list(rows)


@pytest.mark.asyncio
async def test_register_writes_an_owner_membership(client, db_session):
    _, org_id = await _register(client, "Acme", "owner@acme.io")
    mems = await _memberships_of(db_session, "owner@acme.io")
    assert len(mems) == 1
    assert mems[0].org_id == org_id and mems[0].role.value == "owner"
    assert mems[0].is_expense_approver is True and mems[0].status == "active"


@pytest.mark.asyncio
async def test_accept_invite_new_user_writes_membership(client, db_session):
    tok, org_id = await _register(client, "Acme", "owner@acme.io")
    invite = (
        await client.post(
            "/api/v1/team/invites", json={"email": "staff@acme.io", "role": "user"}, headers=_h(tok)
        )
    ).json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "Staff", "password": "supersecret"},
    )
    assert acc.status_code == 201
    mems = await _memberships_of(db_session, "staff@acme.io")
    assert len(mems) == 1 and mems[0].org_id == org_id and mems[0].role.value == "user"


@pytest.mark.asyncio
async def test_existing_account_joins_a_second_org(client, db_session):
    # Person owns Org B.
    await _register(client, "Beta", "person@x.io")
    # Org A invites that same email — allowed (multi-org join), not a 409.
    a_tok, a_org = await _register(client, "Alpha", "a-owner@x.io")
    inv = await client.post(
        "/api/v1/team/invites", json={"email": "person@x.io", "role": "admin"}, headers=_h(a_tok)
    )
    assert inv.status_code == 201, inv.text
    invite = inv.json()["token"]

    # Accepting with the EXISTING account's password adds a membership in Org A.
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "ignored", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text

    mems = await _memberships_of(db_session, "person@x.io")
    assert {m.org_id for m in mems} == {a_org} | {m.org_id for m in mems if m.org_id != a_org}
    assert len(mems) == 2  # Org B (register) + Org A (join)
    a_mem = next(m for m in mems if m.org_id == a_org)
    assert a_mem.role.value == "admin"


@pytest.mark.asyncio
async def test_existing_account_join_wrong_password_is_401(client):
    await _register(client, "Beta", "person@x.io")
    a_tok, _ = await _register(client, "Alpha", "a-owner@x.io")
    invite = (
        await client.post(
            "/api/v1/team/invites", json={"email": "person@x.io", "role": "user"}, headers=_h(a_tok)
        )
    ).json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "x", "password": "WRONG-password"},
    )
    assert acc.status_code == 401


@pytest.mark.asyncio
async def test_inviting_someone_already_a_member_is_409(client):
    a_tok, _ = await _register(client, "Alpha", "a-owner@x.io")
    # The owner is already a member of their own org.
    r = await client.post(
        "/api/v1/team/invites", json={"email": "a-owner@x.io", "role": "user"}, headers=_h(a_tok)
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_no_duplicate_membership_rows(client, db_session):
    # Sanity: the dual-write never creates a second membership for the same slot.
    await _register(client, "Acme", "owner@acme.io")
    n = await db_session.scalar(select(func.count(Membership.id)))
    assert n == 1
