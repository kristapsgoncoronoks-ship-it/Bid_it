"""One account's independently authenticated sessions must not repoint each other."""

import uuid

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.services import memberships


def headers(token, org_id=None):
    out = {"Authorization": f"Bearer {token}"}
    if org_id is not None:
        out["X-Expected-Org-ID"] = org_id
    return out


async def pair(client, db):
    email = f"session-{uuid.uuid4().hex}@example.com"
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Synthetic Alpha",
            "name": "Synthetic Owner",
            "email": email,
            "password": "synthetic-password",
        },
    )
    assert registered.status_code == 201, registered.text
    body = registered.json()
    logged_in = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "synthetic-password"}
    )
    assert logged_in.status_code == 200
    second = Organization(name="Synthetic Beta")
    db.add(second)
    await db.flush()
    await memberships.ensure(
        db,
        org_id=second.id,
        user_id=body["user"]["id"],
        role=UserRole.accountant,
        email=email,
        name="Synthetic Owner",
    )
    await db.commit()
    return (
        body["token"]["access_token"],
        logged_in.json()["token"]["access_token"],
        body["organization"]["id"],
        second.id,
        body["user"]["id"],
    )


INVOICE = {
    "invoice_number": "SESSION-1",
    "vendor_name": "Synthetic Supplier",
    "issue_date": "2026-10-15",
    "currency": "EUR",
    "line_items": [
        {"description": "Service", "quantity": "1", "unit_price": "100", "tax_rate": "21"}
    ],
}


async def assert_independent_sessions(client, db):
    one, two, alpha, beta, uid = await pair(client, db)
    switched = await client.post(f"/api/v1/auth/switch-org/{beta}", headers=headers(two, alpha))
    assert switched.status_code == 200, switched.text
    a = await client.get("/api/v1/auth/me", headers=headers(one))
    b = await client.get("/api/v1/auth/me", headers=headers(two))
    assert a.json()["organization"]["id"] == alpha
    assert a.json()["user"]["role"] == "owner"
    assert a.json()["user"]["is_expense_approver"] is True
    assert b.json()["organization"]["id"] == beta
    assert b.json()["user"]["role"] == "accountant"
    assert b.json()["user"]["is_expense_approver"] is False
    assert "member.manage" not in b.json()["permissions"]
    write = await client.post("/api/v1/invoices", json=INVOICE, headers=headers(one, alpha))
    assert write.status_code == 201, write.text
    assert (await client.get("/api/v1/invoices", headers=headers(one))).json()["total"] == 1
    assert (await client.get("/api/v1/invoices", headers=headers(two))).json()["total"] == 0
    selected_a = (await client.get("/api/v1/auth/organizations", headers=headers(one))).json()
    selected_b = (await client.get("/api/v1/auth/organizations", headers=headers(two))).json()
    assert next(row["org_id"] for row in selected_a if row["current"]) == alpha
    assert next(row["org_id"] for row in selected_b if row["current"]) == beta
    user = await db.get(User, uid, populate_existing=True)
    assert user.org_id == alpha and user.role == UserRole.owner
    event = await db.scalar(
        select(AuditEvent).where(AuditEvent.org_id == beta, AuditEvent.action == "auth.switch_org")
    )
    # The event's exact action spelling is defined centrally; assert attribution below.
    if event is None:
        from app.services import audit

        event = await db.scalar(
            select(AuditEvent).where(
                AuditEvent.org_id == beta, AuditEvent.action == audit.A.SWITCH_ORG
            )
        )
    assert event is not None and event.session_id is not None
    return one, two, alpha, beta, uid


@pytest.mark.asyncio
async def test_sessions_have_independent_tenants_roles_and_writes(client, db_session):
    await assert_independent_sessions(client, db_session)


@pytest.mark.asyncio
async def test_stale_tab_cannot_write_or_switch_with_the_previous_org(client, db_session):
    one, two, alpha, beta, uid = await pair(client, db_session)
    assert (
        await client.post(f"/api/v1/auth/switch-org/{beta}", headers=headers(two, alpha))
    ).status_code == 200
    stale = await client.post("/api/v1/invoices", json=INVOICE, headers=headers(two, alpha))
    assert stale.status_code == 409 and stale.json()["code"] == "identity_changed"
    assert (await client.get("/api/v1/invoices", headers=headers(two, beta))).json()["total"] == 0
    stale_switch = await client.post(
        f"/api/v1/auth/switch-org/{alpha}", headers=headers(two, alpha)
    )
    assert stale_switch.status_code == 409
    wrong_user = await client.get(
        "/api/v1/auth/me", headers={**headers(one, alpha), "X-Expected-User-ID": str(uuid.uuid4())}
    )
    assert wrong_user.status_code == 409
    assert (await client.get("/api/v1/auth/me", headers=headers(two))).json()["organization"][
        "id"
    ] == beta


@pytest.mark.asyncio
async def test_suspended_membership_blocks_only_its_selected_session(client, db_session):
    one, two, alpha, beta, uid = await pair(client, db_session)
    assert (
        await client.post(f"/api/v1/auth/switch-org/{beta}", headers=headers(two))
    ).status_code == 200
    membership = await db_session.scalar(
        select(Membership).where(Membership.org_id == beta, Membership.user_id == uid)
    )
    membership.status = "suspended"
    await db_session.commit()
    assert (await client.get("/api/v1/auth/me", headers=headers(two))).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=headers(one))).status_code == 200
    assert (
        await client.post(f"/api/v1/auth/switch-org/{beta}", headers=headers(one))
    ).status_code == 404


@pytest.mark.asyncio
async def test_switch_refuses_suspended_org_without_repointing_session(client, db_session):
    one, two, alpha, beta, uid = await pair(client, db_session)
    org = await db_session.get(Organization, beta)
    org.status = "suspended"
    await db_session.commit()
    refused = await client.post(f"/api/v1/auth/switch-org/{beta}", headers=headers(two))
    assert refused.status_code == 404
    assert (await client.get("/api/v1/auth/me", headers=headers(two))).json()["organization"][
        "id"
    ] == alpha


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["membership", "organization"])
async def test_password_login_recovers_into_an_active_membership(client, db_session, unavailable):
    one, two, alpha, beta, uid = await pair(client, db_session)
    user = await db_session.get(User, uid)
    if unavailable == "membership":
        await memberships.set_status(db_session, alpha, uid, "suspended")
    else:
        org = await db_session.get(Organization, alpha)
        org.status = "suspended"
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "synthetic-password"}
    )
    assert login.status_code == 200
    assert login.json()["organization"]["id"] == beta
    assert login.json()["user"]["role"] == "accountant"
    token = login.json()["token"]["access_token"]
    identity = await client.get("/api/v1/auth/me", headers=headers(token, beta))
    assert identity.status_code == 200
    assert identity.json()["organization"]["id"] == beta
    await db_session.refresh(user)
    assert user.org_id == alpha and user.role == UserRole.owner
