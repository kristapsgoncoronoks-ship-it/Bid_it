"""Multi-step routed expense approvals: policy-driven ordered chains, sequential
step advance with queue-jump prevention, reassignment, amount-threshold routing,
and policy CRUD — mirrors the invoice approval-engine tests."""

import pytest

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _activate(auth_client):
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    ).status_code == 200


async def _member(auth_client, client, email, name="Emp"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": "user"})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": name, "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"], acc.json()["user"]["id"]


async def _appoint(auth_client, uid):
    r = await auth_client.patch(f"/api/v1/team/members/{uid}", json={"is_expense_approver": True})
    assert r.status_code == 200, r.text


async def _submit(client, emp, amount="42.00"):
    rid = (
        await client.post(
            "/api/v1/expenses",
            headers=_h(emp),
            json={
                "title": "Trip",
                "items": [
                    {
                        "spend_date": "2026-05-01",
                        "category": "meals",
                        "description": "Dinner",
                        "amount": amount,
                        "vat_amount": "0",
                    }
                ],
            },
        )
    ).json()["id"]
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp))).json()
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Client visit"},
            headers=_h(emp),
        )
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("r.png", _PNG, "image/png")},
            headers=_h(emp),
        )
    s = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert s.status_code == 200, s.text
    return rid


def _dec(client, rid, action, token):
    return client.post(
        f"/api/v1/expenses/{rid}/decision", headers=_h(token), json={"action": action}
    )


@pytest.mark.asyncio
async def test_sequential_two_approver_chain(auth_client, client):
    await _activate(auth_client)
    owner = auth_client.headers["Authorization"].split()[1]
    a_tok, a_id = await _member(auth_client, client, "a@corp.io", "Ann")
    b_tok, b_id = await _member(auth_client, client, "b@corp.io", "Bob")
    await _appoint(auth_client, a_id)
    await _appoint(auth_client, b_id)

    pol = await auth_client.post(
        "/api/v1/expenses/approval-policies",
        json={"name": "Two-step", "approver_ids": [a_id, b_id]},
    )
    assert pol.status_code == 201, pol.text

    # The owner submits their OWN report; the chain is Ann → Bob (neither is the
    # owner, so segregation of duties is satisfied).
    rid = await _submit(client, owner)
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    assert len(detail["approval_steps"]) == 2
    assert detail["status"] == "submitted"

    # B cannot jump the queue — step 0 is Ann's.
    assert (await _dec(client, rid, "approve", b_tok)).status_code == 403
    # Ann approves → partially approved; Bob's step still pending.
    r1 = await _dec(client, rid, "approve", a_tok)
    assert r1.status_code == 200 and r1.json()["status"] == "partially_approved"
    # Bob approves → fully approved.
    r2 = await _dec(client, rid, "approve", b_tok)
    assert r2.status_code == 200 and r2.json()["status"] == "approved"
    steps = r2.json()["approval_steps"]
    assert [s["status"] for s in steps] == ["approved", "approved"]


@pytest.mark.asyncio
async def test_reassign_pending_step(auth_client, client):
    await _activate(auth_client)
    owner = auth_client.headers["Authorization"].split()[1]
    a_tok, a_id = await _member(auth_client, client, "a2@corp.io")
    b_tok, b_id = await _member(auth_client, client, "b2@corp.io")
    await _appoint(auth_client, a_id)
    await _appoint(auth_client, b_id)
    await auth_client.post(
        "/api/v1/expenses/approval-policies", json={"name": "One", "approver_ids": [a_id]}
    )
    rid = await _submit(client, owner)

    # Reassign the pending step from Ann to Bob.
    rr = await auth_client.post(f"/api/v1/expenses/{rid}/reassign", json={"approver_id": b_id})
    assert rr.status_code == 200, rr.text
    # Ann can no longer act; Bob can.
    assert (await _dec(client, rid, "approve", a_tok)).status_code == 403
    assert (await _dec(client, rid, "approve", b_tok)).json()["status"] == "approved"


@pytest.mark.asyncio
async def test_amount_threshold_routing(auth_client, client):
    await _activate(auth_client)
    a_tok, a_id = await _member(auth_client, client, "a3@corp.io")
    emp_tok, _ = await _member(auth_client, client, "emp3@corp.io")
    await _appoint(auth_client, a_id)
    # Policy applies only to reports >= 1000 EUR.
    await auth_client.post(
        "/api/v1/expenses/approval-policies",
        json={"name": "Big spend", "min_amount": "1000", "approver_ids": [a_id]},
    )

    # Below threshold → a generic open step; the owner (any approver) can approve.
    small = await _submit(client, emp_tok, "42.00")
    d_small = (await auth_client.get(f"/api/v1/expenses/{small}")).json()
    assert d_small["approval_steps"][0]["approver_id"] is None
    approved_small = await auth_client.post(
        f"/api/v1/expenses/{small}/decision", json={"action": "approve"}
    )
    assert approved_small.json()["status"] == "approved"

    # At/above threshold → routed to Ann specifically.
    big = await _submit(client, emp_tok, "1500.00")
    d_big = (await auth_client.get(f"/api/v1/expenses/{big}")).json()
    assert d_big["approval_steps"][0]["approver_id"] == a_id
    # The owner is not Ann → cannot decide Ann's named step.
    assert (
        await auth_client.post(f"/api/v1/expenses/{big}/decision", json={"action": "approve"})
    ).status_code == 403
    assert (await _dec(client, big, "approve", a_tok)).json()["status"] == "approved"


@pytest.mark.asyncio
async def test_return_rebuilds_chain_on_resubmit(auth_client, client):
    await _activate(auth_client)
    a_tok, a_id = await _member(auth_client, client, "a4@corp.io")
    emp_tok, _ = await _member(auth_client, client, "emp4@corp.io")
    await _appoint(auth_client, a_id)
    await auth_client.post(
        "/api/v1/expenses/approval-policies", json={"name": "One", "approver_ids": [a_id]}
    )
    rid = await _submit(client, emp_tok)
    ret = await _dec(client, rid, "return_for_correction", a_tok)
    assert ret.json()["status"] == "returned"
    # Resubmit builds a fresh, all-pending chain.
    again = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp_tok))
    assert again.status_code == 200
    steps = again.json()["approval_steps"]
    assert len(steps) == 1 and steps[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_approval_policy_crud_and_version_guard(auth_client, client):
    await _activate(auth_client)
    _, a_id = await _member(auth_client, client, "a5@corp.io")
    created = await auth_client.post(
        "/api/v1/expenses/approval-policies",
        json={"name": "P", "approver_ids": [a_id], "priority": 50},
    )
    assert created.status_code == 201
    pid, ver = created.json()["id"], created.json()["version"]

    # Stale version → 409.
    stale = await auth_client.patch(
        f"/api/v1/expenses/approval-policies/{pid}",
        json={"name": "P2", "approver_ids": [a_id], "version": ver + 5},
    )
    assert stale.status_code == 409
    ok = await auth_client.patch(
        f"/api/v1/expenses/approval-policies/{pid}",
        json={"name": "P2", "approver_ids": [a_id], "active": False, "version": ver},
    )
    assert ok.status_code == 200 and ok.json()["version"] == ver + 1

    # Non-admin cannot manage policies.
    emp_tok, _ = await _member(auth_client, client, "emp5@corp.io")
    forbidden = await client.post(
        "/api/v1/expenses/approval-policies",
        headers=_h(emp_tok),
        json={"name": "X", "approver_ids": []},
    )
    assert forbidden.status_code == 403

    assert (
        await auth_client.delete(f"/api/v1/expenses/approval-policies/{pid}")
    ).status_code == 204
