"""Membership-driven roster (Slice 6e).

The member list reads only memberships, so it is complete — a member who is
currently active in ANOTHER org still appears here with their per-org role — while
staying strictly org-scoped (no other org's members leak in).
"""

import pytest


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


async def _register(client, org, email):
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": org,
            "name": email.split("@")[0],
            "email": email,
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"], r.json()["organization"]["id"]


@pytest.mark.asyncio
async def test_member_active_in_another_org_still_on_roster(client):
    # Person owns their own org P (so their ACTIVE org is P, not B).
    p_tok, _ = await _register(client, "PersonCo", "person@x.io")
    # Beta's owner invites Person into Beta; Person accepts (multi-org join).
    b_tok, b_org = await _register(client, "Beta", "b-owner@x.io")
    invite = (
        await client.post(
            "/api/v1/team/invites",
            json={"email": "person@x.io", "role": "user"},
            headers=_h(b_tok),
        )
    ).json()["token"]
    assert (
        await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": invite, "name": "Person", "password": "supersecret"},
        )
    ).status_code == 201

    # Person's active org is still PersonCo — confirm.
    assert (await client.get("/api/v1/auth/me", headers=_h(p_tok))).json()["organization"][
        "name"
    ] == "PersonCo"

    # Beta's roster INCLUDES Person (active elsewhere) with their Beta role.
    roster = (await client.get("/api/v1/team/members", headers=_h(b_tok))).json()
    by_email = {m["email"]: m for m in roster}
    assert set(by_email) == {"b-owner@x.io", "person@x.io"}
    assert by_email["person@x.io"]["role"] == "user"  # their role IN Beta
    assert by_email["b-owner@x.io"]["role"] == "owner"


@pytest.mark.asyncio
async def test_roster_does_not_leak_other_orgs(client):
    a_tok, _ = await _register(client, "Alpha", "a@x.io")
    b_tok, _ = await _register(client, "Beta", "b@x.io")
    # Each owner sees only their own org's single member.
    a_roster = (await client.get("/api/v1/team/members", headers=_h(a_tok))).json()
    b_roster = (await client.get("/api/v1/team/members", headers=_h(b_tok))).json()
    assert [m["email"] for m in a_roster] == ["a@x.io"]
    assert [m["email"] for m in b_roster] == ["b@x.io"]


@pytest.mark.asyncio
async def test_role_change_for_switched_away_member_takes_effect_on_switch(client):
    # Person owns P; joins Beta as 'user'. Beta owner promotes them to admin while
    # Person is active in P. When Person switches to Beta, they are admin there.
    p_tok, _ = await _register(client, "PersonCo", "person@x.io")
    b_tok, b_org = await _register(client, "Beta", "b-owner@x.io")
    invite = (
        await client.post(
            "/api/v1/team/invites",
            json={"email": "person@x.io", "role": "user"},
            headers=_h(b_tok),
        )
    ).json()["token"]
    await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite, "name": "Person", "password": "supersecret"},
    )

    # Find Person's user id from Beta's roster and promote to admin.
    roster = (await client.get("/api/v1/team/members", headers=_h(b_tok))).json()
    person_id = next(m["id"] for m in roster if m["email"] == "person@x.io")
    r = await client.patch(
        f"/api/v1/team/members/{person_id}", json={"role": "admin"}, headers=_h(b_tok)
    )
    assert r.status_code == 200 and r.json()["role"] == "admin"

    # Person switches into Beta → they are now admin there (membership drove it).
    assert (
        await client.post(f"/api/v1/auth/switch-org/{b_org}", headers=_h(p_tok))
    ).status_code == 200
    me = (await client.get("/api/v1/auth/me", headers=_h(p_tok))).json()
    assert me["organization"]["id"] == b_org and me["user"]["role"] == "admin"
