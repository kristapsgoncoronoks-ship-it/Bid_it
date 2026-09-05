"""PROD-001 (audit 2026-09-05) — a suspended tenant can still reach the screen
that restores it.

`billing.charge_renewal` and the Stripe status map set `org.status =
"suspended"` on a declined payment. The active-only identity gate then answered
401 to EVERY request from every member: `GET /auth/me` (so the SPA could not
boot), and `/billing/*` (so the owner could not reach the card form). A missed
card payment on a €99 plan ejected an SME from its own finance system with no
way back in except support.

The fix is narrow and the tests hold it narrow:

- the identity read and the billing surface answer for a SUSPENDED org;
- every data route still answers 401 `organization_suspended` on the very next
  request (WO-4's promise, re-asserted in test_org_suspension.py);
- a CANCELED org is still a 401 everywhere, billing included;
- the permission is unchanged: a non-owner member of a suspended org sees their
  identity (with the status) and a 403 on billing, not a way in.

The grace policy — how long a delinquent tenant keeps working before
suspension, and what a dunning ladder emails — is the owner's decision
(DECISIONS-NEEDED §18); nothing here changes WHEN a tenant is suspended.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.organization import Organization
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


async def _set_status(db_session, email: str, status: str) -> str:
    user = await db_session.scalar(select(User).where(User.email == email))
    org = await db_session.get(Organization, user.org_id)
    org.status = status
    await db_session.commit()
    return org.id


@pytest.mark.asyncio
async def test_prod001_the_owner_of_a_suspended_org_reaches_billing(client, db_session):
    tok = await _register(client, "Haulage Co", "owner@haulage.example")
    await _set_status(db_session, "owner@haulage.example", "suspended")

    me = await client.get("/api/v1/auth/me", headers=_h(tok))
    assert me.status_code == 200, me.text
    assert me.json()["organization"]["status"] == "suspended"

    billing = await client.get("/api/v1/billing", headers=_h(tok))
    assert billing.status_code == 200, billing.text
    assert billing.json()["status"] == "suspended"
    assert billing.json()["available_plans"], "the plans to choose from are served"

    # The card-fixing doors are not 401. Billing is off in the harness, so they
    # answer the same 4xx an active org would get — never the suspension 401.
    portal = await client.post("/api/v1/billing/portal", json={}, headers=_h(tok))
    assert portal.status_code != 401, portal.text
    checkout = await client.post("/api/v1/billing/checkout", json={"plan": "pro"}, headers=_h(tok))
    assert checkout.status_code != 401, checkout.text

    # And the data routes still refuse on the very next request.
    for path in ("/api/v1/invoices", "/api/v1/team/members", "/api/v1/dashboard"):
        r = await client.get(path, headers=_h(tok))
        assert r.status_code == 401, (path, r.text)
        assert r.json()["code"] == "organization_suspended"


@pytest.mark.asyncio
async def test_prod001_a_canceled_org_is_still_locked_out_of_billing(client, db_session):
    tok = await _register(client, "Gone Co", "owner@gone.example")
    await _set_status(db_session, "owner@gone.example", "canceled")
    for path in ("/api/v1/auth/me", "/api/v1/billing", "/api/v1/invoices"):
        r = await client.get(path, headers=_h(tok))
        assert r.status_code == 401, (path, r.text)
        assert r.json()["code"] == "organization_suspended"


@pytest.mark.asyncio
async def test_prod001_a_non_owner_in_a_suspended_org_sees_the_status_but_not_billing(
    client, db_session
):
    owner_tok = await _register(client, "Site Crew OU", "owner@sitecrew.example")
    invite = (
        await client.post(
            "/api/v1/team/invites",
            json={"email": "driver@sitecrew.example", "role": "user"},
            headers=_h(owner_tok),
        )
    ).json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "Driver", "password": "supersecret"},
    )
    staff_tok = acc.json()["token"]["access_token"]
    await _set_status(db_session, "owner@sitecrew.example", "suspended")

    me = await client.get("/api/v1/auth/me", headers=_h(staff_tok))
    assert me.status_code == 200
    assert me.json()["organization"]["status"] == "suspended"
    # Same permission boundary as before: BILLING_MANAGE is the owner's.
    assert (await client.get("/api/v1/billing", headers=_h(staff_tok))).status_code == 403
    assert (await client.get("/api/v1/invoices", headers=_h(staff_tok))).status_code == 401


@pytest.mark.asyncio
async def test_prod001_the_active_only_gate_is_untouched_for_an_active_org(client):
    tok = await _register(client, "Active Co", "owner@active.example")
    assert (await client.get("/api/v1/auth/me", headers=_h(tok))).status_code == 200
    assert (await client.get("/api/v1/billing", headers=_h(tok))).status_code == 200
    assert (await client.get("/api/v1/invoices", headers=_h(tok))).status_code == 200
