"""Public ICS capabilities stop at the same live-access boundary as the app."""

import pytest
from sqlalchemy import select

from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.services import memberships
from tests.test_schedule_feed import _assign, _project


async def staff(auth_client, client):
    invited = await auth_client.post(
        "/api/v1/team/invites", json={"email": "calendar-staff@example.com", "role": "user"}
    )
    accepted = await client.post(
        "/api/v1/auth/accept-invite",
        json={
            "token": invited.json()["token"],
            "name": "Synthetic Staff",
            "password": "synthetic-password",
        },
    )
    assert accepted.status_code == 201
    body = accepted.json()
    return body["user"]["id"], {"Authorization": "Bearer " + body["token"]["access_token"]}


@pytest.mark.asyncio
async def test_offboarding_and_reactivation_never_revive_the_old_calendar_url(auth_client, client):
    uid, h = await staff(auth_client, client)
    project = await _project(auth_client, "SYNTHETIC-PRIVATE")
    await _assign(auth_client, project, uid, note="Synthetic confidential schedule note")
    feed = await client.get("/api/v1/schedule/feed-token", headers=h)
    path = feed.json()["path"]
    active = await client.get(path)
    assert active.status_code == 200 and "Synthetic confidential schedule note" in active.text
    assert active.headers["cache-control"] == "private, no-store"
    assert (
        await auth_client.patch(f"/api/v1/team/members/{uid}", json={"is_active": False})
    ).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=h)).status_code == 401
    denied = await client.get(path)
    unknown = await client.get("/api/v1/calendar/feed/unknown.ics")
    assert denied.status_code == unknown.status_code == 404
    assert denied.json() == unknown.json()
    assert "Synthetic confidential" not in denied.text
    assert (
        await auth_client.patch(f"/api/v1/team/members/{uid}", json={"is_active": True})
    ).status_code == 200
    assert (await client.get(path)).status_code == 404
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "calendar-staff@example.com", "password": "synthetic-password"},
    )
    new_h = {"Authorization": "Bearer " + login.json()["token"]["access_token"]}
    replacement = await client.get("/api/v1/schedule/feed-token", headers=new_h)
    assert replacement.json()["path"] != path
    assert (await client.get(replacement.json()["path"])).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ended",
    [
        "membership_suspended",
        "membership_removed",
        "account_disabled",
        "org_suspended",
        "org_canceled",
    ],
)
async def test_live_public_resolver_checks_each_access_gate(auth_client, db_session, ended):
    me = (await auth_client.get("/api/v1/auth/me")).json()
    path = (await auth_client.get("/api/v1/schedule/feed-token")).json()["path"]
    uid, org_id = me["user"]["id"], me["organization"]["id"]
    if ended.startswith("membership"):
        row = await db_session.scalar(
            select(Membership).where(Membership.org_id == org_id, Membership.user_id == uid)
        )
        if ended == "membership_removed":
            await db_session.delete(row)
        else:
            row.status = "suspended"
    elif ended == "account_disabled":
        user = await db_session.get(User, uid)
        user.is_active = False
    else:
        org = await db_session.get(Organization, org_id)
        org.status = "canceled" if ended == "org_canceled" else "suspended"
    await db_session.commit()
    denied = await auth_client.get(path)
    assert denied.status_code == 404 and denied.json()["detail"] == "unknown feed"


@pytest.mark.asyncio
async def test_membership_invalidation_preserves_other_org_calendar(auth_client, db_session):
    me = (await auth_client.get("/api/v1/auth/me")).json()
    uid, alpha = me["user"]["id"], me["organization"]["id"]
    path_a = (await auth_client.get("/api/v1/schedule/feed-token")).json()["path"]
    beta = Organization(name="Synthetic Other Workspace")
    db_session.add(beta)
    await db_session.flush()
    await memberships.ensure(db_session, org_id=beta.id, user_id=uid, role=UserRole.user)
    await db_session.commit()
    assert (await auth_client.post(f"/api/v1/auth/switch-org/{beta.id}")).status_code == 200
    path_b = (await auth_client.get("/api/v1/schedule/feed-token")).json()["path"]
    assert (await auth_client.get(path_a)).status_code == 200
    await memberships.set_status(db_session, alpha, uid, "suspended")
    await db_session.commit()
    assert (await auth_client.get(path_a)).status_code == 404

    assert (await auth_client.get(path_b)).status_code == 200
    await memberships.set_status(db_session, alpha, uid, "active")
    await db_session.commit()
    assert (await auth_client.get(path_a)).status_code == 404


@pytest.mark.asyncio
async def test_platform_org_reactivation_does_not_revive_calendar_urls(auth_client, db_session):
    me = (await auth_client.get("/api/v1/auth/me")).json()
    uid, org_id = me["user"]["id"], me["organization"]["id"]
    user = await db_session.get(User, uid)
    user.is_platform_admin = True
    await db_session.commit()
    path = (await auth_client.get("/api/v1/schedule/feed-token")).json()["path"]
    assert (
        await auth_client.patch(f"/api/v1/platform/tenants/{org_id}", json={"status": "suspended"})
    ).status_code == 200
    assert (await auth_client.get(path)).status_code == 404
    # Platform routes deliberately authenticate without the revoked tenant session;
    # use a new independent active operator to restore the organization.
    registered = await auth_client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Synthetic Operator",
            "name": "Operator",
            "email": "operator-calendar@example.com",
            "password": "synthetic-password",
        },
    )
    operator = registered.json()
    row = await db_session.get(User, operator["user"]["id"])
    row.is_platform_admin = True
    await db_session.commit()
    h = {"Authorization": "Bearer " + operator["token"]["access_token"]}
    assert (
        await auth_client.patch(
            f"/api/v1/platform/tenants/{org_id}", json={"status": "active"}, headers=h
        )
    ).status_code == 200
    assert (await auth_client.get(path, headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_team_offboarding_does_not_disable_unrelated_session_or_feed(
    auth_client, client, db_session
):
    uid, h_a = await staff(auth_client, client)
    path_a = (await client.get("/api/v1/schedule/feed-token", headers=h_a)).json()["path"]
    beta = Organization(name="Synthetic Independent Calendar")
    db_session.add(beta)
    await db_session.flush()
    await memberships.ensure(db_session, org_id=beta.id, user_id=uid, role=UserRole.user)
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "calendar-staff@example.com", "password": "synthetic-password"},
    )
    h_b = {"Authorization": "Bearer " + login.json()["token"]["access_token"]}
    assert (await client.post(f"/api/v1/auth/switch-org/{beta.id}", headers=h_b)).status_code == 200
    path_b = (await client.get("/api/v1/schedule/feed-token", headers=h_b)).json()["path"]
    assert (
        await auth_client.patch(f"/api/v1/team/members/{uid}", json={"is_active": False})
    ).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=h_a)).status_code == 401
    assert (await client.get(path_a)).status_code == 404
    assert (await client.get("/api/v1/auth/me", headers=h_b)).status_code == 200
    assert (await client.get(path_b)).status_code == 200
