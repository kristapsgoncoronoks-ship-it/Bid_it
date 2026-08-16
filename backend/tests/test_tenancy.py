"""Multi-tenant SaaS: team/invites, plans + seats, module-by-plan gating,
suspension, and the platform operator view."""

import pytest
from sqlalchemy import select

from app.models.user import User
from app.services.plans import PLANS, plan_for


@pytest.mark.asyncio
async def test_billing_defaults_trial(auth_client):
    b = (await auth_client.get("/api/v1/billing")).json()
    assert b["plan"]["key"] == "trial"
    assert b["status"] == "active"
    assert b["seats_used"] == 1
    assert b["seats_limit"] == plan_for("trial").seats
    # The route must offer the WHOLE ladder — nothing quietly hidden, nothing
    # invented. Derived rather than frozen as a literal: this test's subject is
    # the DEFAULT a new org lands on, and a second hand-written copy of the
    # ladder here just goes stale the next time the commercial one changes (it
    # did, on 2026-08-15 — §2a). What the ladder actually CONTAINS — the caps,
    # the prices, which tiers are paid — is pinned in one place,
    # `test_access.py::test_matrix_covers_every_plan_with_its_defaults`.
    assert {p["key"] for p in b["available_plans"]} == set(PLANS)


@pytest.mark.asyncio
async def test_invite_accept_creates_member_in_same_tenant(auth_client, client):
    # owner invites a member
    inv = await auth_client.post(
        "/api/v1/team/invites", json={"email": "colleague@acme.io", "role": "user"}
    )
    assert inv.status_code == 201, inv.text
    token = inv.json()["token"]

    # preview (unauthenticated)
    prev = await client.get(f"/api/v1/auth/invite/{token}")
    assert prev.json()["organization_name"] == "Acme"
    assert prev.json()["email"] == "colleague@acme.io"

    # accept → new user, logged in, in the SAME org
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "name": "Colleague", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    new_token = acc.json()["token"]["access_token"]
    assert acc.json()["organization"]["name"] == "Acme"

    # the new member sees the same tenant's data surface
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert me.json()["organization"]["id"] == acc.json()["organization"]["id"]

    # seat count is now 2, and both members are listed
    b = (await auth_client.get("/api/v1/billing")).json()
    assert b["seats_used"] == 2
    members = (await auth_client.get("/api/v1/team/members")).json()
    assert {m["email"] for m in members} == {"owner@acme.io", "colleague@acme.io"}

    # invite can't be reused
    again = await client.post(
        "/api/v1/auth/accept-invite", json={"token": token, "name": "X", "password": "supersecret"}
    )
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_seat_limit_enforced(auth_client):
    """Invites are refused once they would take the org past its plan's seats.

    The seat count is read from the plan rather than written here. It used to be
    hardcoded ("Starter, 2 seats") and the arithmetic silently stopped testing
    anything the day Starter became 3 seats (§2a, 2026-08-15): the first extra
    invite still returned 201, so the assertion that it *exceeded* the cap was
    passing on a request that was simply within it. The subject is the refusal at
    the boundary, not the number — so fill the plan up whatever it is, then prove
    the next one is refused."""
    await auth_client.put("/api/v1/billing/plan", json={"plan": "starter"})
    seats = plan_for("starter").seats
    assert seats >= 2, "this test needs a plan with room for at least one invite"

    # Owner already occupies one seat; fill the rest exactly.
    for i in range(seats - 1):
        ok = await auth_client.post("/api/v1/team/invites", json={"email": f"seat{i}@acme.io"})
        assert ok.status_code == 201, ok.text

    full = await auth_client.post("/api/v1/team/invites", json={"email": "overflow@acme.io"})
    assert full.status_code == 402, full.text  # would exceed the plan


@pytest.mark.asyncio
async def test_module_gated_by_plan(auth_client):
    # Starter does not include issuing
    await auth_client.put("/api/v1/billing/plan", json={"plan": "starter"})
    blocked = await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    assert blocked.status_code == 402

    # Pro includes issuing
    await auth_client.put("/api/v1/billing/plan", json={"plan": "pro"})
    ok = await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    assert ok.status_code == 200 and ok.json()["enabled"] is True


@pytest.mark.asyncio
async def test_downgrade_disables_addon_and_guards_seats(auth_client):
    await auth_client.put("/api/v1/billing/plan", json={"plan": "pro"})
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    # downgrading to starter turns issuing back off (not in plan)
    await auth_client.put("/api/v1/billing/plan", json={"plan": "starter"})
    mods = {m["key"]: m for m in (await auth_client.get("/api/v1/modules")).json()}
    assert mods["issuing"]["enabled"] is False


@pytest.mark.asyncio
async def test_last_owner_cannot_be_demoted(auth_client):
    me = (await auth_client.get("/api/v1/auth/me")).json()
    r = await auth_client.patch(f"/api/v1/team/members/{me['user']['id']}", json={"role": "user"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_suspended_org_blocks_login(auth_client, client, db_session):
    from app.models.organization import Organization

    me = (await auth_client.get("/api/v1/auth/me")).json()
    org = await db_session.get(Organization, me["organization"]["id"])
    org.status = "suspended"
    await db_session.commit()

    r = await client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.io", "password": "supersecret"}
    )
    assert r.status_code == 402


@pytest.mark.asyncio
async def test_platform_admin_lists_and_suspends_tenants(auth_client, client, db_session):
    # non-admin is refused
    assert (await auth_client.get("/api/v1/platform/tenants")).status_code == 403

    # grant platform admin on the owner (operator provisioning is out-of-band)
    owner = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    owner.is_platform_admin = True
    await db_session.commit()

    tenants = await auth_client.get("/api/v1/platform/tenants")
    assert tenants.status_code == 200
    assert any(t["name"] == "Acme" for t in tenants.json())
    tid = tenants.json()[0]["id"]

    upd = await auth_client.patch(
        f"/api/v1/platform/tenants/{tid}", json={"status": "suspended", "plan": "pro"}
    )
    assert upd.status_code == 200
    assert upd.json()["status"] == "suspended" and upd.json()["plan"] == "pro"
