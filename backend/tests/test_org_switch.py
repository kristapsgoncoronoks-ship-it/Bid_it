"""Active org in session + organization switching (Slice 6c).

A user who belongs to several orgs can list them and switch the active one; the
switch changes what tenant data and which role the session sees, and switching to
an org you don't belong to is an opaque 404 — isolation is preserved.
"""

import pytest


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


async def _register(client, org, email):
    r = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "name": "P", "email": email, "password": "supersecret"},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"], r.json()["organization"]["id"]


async def _join_second_org(client, *, joiner_email, host_token):
    """Invite an existing account into the host's org and accept it (6b flow)."""
    invite = (
        await client.post(
            "/api/v1/team/invites",
            json={"email": joiner_email, "role": "user"},
            headers=_h(host_token),
        )
    ).json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "P", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text


@pytest.mark.asyncio
async def test_single_org_user_lists_one_org(client):
    tok, org_id = await _register(client, "Solo", "solo@x.io")
    orgs = (await client.get("/api/v1/auth/organizations", headers=_h(tok))).json()
    assert len(orgs) == 1
    assert orgs[0]["org_id"] == org_id and orgs[0]["current"] is True


@pytest.mark.asyncio
async def test_switch_changes_active_org_role_and_data(client):
    # Person owns Org B; also joins Org A as a plain user.
    b_tok, b_org = await _register(client, "Beta", "person@x.io")
    a_tok, a_org = await _register(client, "Alpha", "a-owner@x.io")
    await _join_second_org(client, joiner_email="person@x.io", host_token=a_tok)

    # As Person (active in Beta), create an invoice in Beta.
    inv = {
        "vendor_name": "BetaVendor",
        "invoice_number": "B-1",
        "issue_date": "2026-06-01",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "x",
                "category": "c",
                "quantity": "1",
                "unit_price": "10",
                "tax_rate": "0",
            }
        ],
    }
    assert (await client.post("/api/v1/invoices", json=inv, headers=_h(b_tok))).status_code == 201
    assert (await client.get("/api/v1/invoices", headers=_h(b_tok))).json()["total"] == 1

    # Person is OWNER in Beta.
    me = (await client.get("/api/v1/auth/me", headers=_h(b_tok))).json()
    assert me["organization"]["id"] == b_org and me["user"]["role"] == "owner"

    # Both orgs are listed.
    orgs = (await client.get("/api/v1/auth/organizations", headers=_h(b_tok))).json()
    assert {o["org_id"] for o in orgs} == {a_org, b_org}

    # Switch to Alpha → active org + role change, and Beta's data is NOT visible.
    sw = await client.post(f"/api/v1/auth/switch-org/{a_org}", headers=_h(b_tok))
    assert sw.status_code == 200 and sw.json()["role"] == "user"
    me2 = (await client.get("/api/v1/auth/me", headers=_h(b_tok))).json()
    assert me2["organization"]["id"] == a_org and me2["user"]["role"] == "user"
    perms = (await client.get("/api/v1/auth/permissions", headers=_h(b_tok))).json()
    assert perms["role"] == "employee"  # 'user' → Employee in the authz matrix
    assert (await client.get("/api/v1/invoices", headers=_h(b_tok))).json()["total"] == 0

    # Switch back to Beta → the invoice is there again.
    assert (
        await client.post(f"/api/v1/auth/switch-org/{b_org}", headers=_h(b_tok))
    ).status_code == 200
    assert (await client.get("/api/v1/invoices", headers=_h(b_tok))).json()["total"] == 1


@pytest.mark.asyncio
async def test_cannot_switch_to_an_org_you_do_not_belong_to(client):
    tok, _ = await _register(client, "Mine", "me@x.io")
    _, other_org = await _register(client, "Theirs", "other@x.io")
    # `me` has no membership in `other_org` → opaque 404.
    r = await client.post(f"/api/v1/auth/switch-org/{other_org}", headers=_h(tok))
    assert r.status_code == 404
    # And still sees only their own org afterwards.
    orgs = (await client.get("/api/v1/auth/organizations", headers=_h(tok))).json()
    assert len(orgs) == 1


@pytest.mark.asyncio
async def test_switch_target_org_data_never_leaks_before_switch(client):
    # Alpha owner has data; Person is NOT a member of Alpha → cannot see it and
    # cannot switch into it.
    a_tok, a_org = await _register(client, "Alpha", "a@x.io")
    inv = {
        "vendor_name": "Secret",
        "invoice_number": "A-1",
        "issue_date": "2026-06-01",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "x",
                "category": "c",
                "quantity": "1",
                "unit_price": "10",
                "tax_rate": "0",
            }
        ],
    }
    await client.post("/api/v1/invoices", json=inv, headers=_h(a_tok))
    p_tok, _ = await _register(client, "Person", "p@x.io")
    assert (
        await client.post(f"/api/v1/auth/switch-org/{a_org}", headers=_h(p_tok))
    ).status_code == 404
    assert (await client.get("/api/v1/invoices", headers=_h(p_tok))).json()["total"] == 0
