"""Designated expense approvers (owner appoints), self-approval prevention, and
verification of expense items against a bank/card statement."""

import io

import pytest


async def _activate(auth_client):
    await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def _member(auth_client, client, email, name="Employee"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": "user"})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": name, "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"], acc.json()["user"]["id"]


_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def _complete_and_submit(client, rid, headers):
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=headers)).json()
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Client visit"},
            headers=headers,
        )
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("r.png", _PNG, "image/png")},
            headers=headers,
        )
    return await client.post(f"/api/v1/expenses/{rid}/submit", headers=headers)


def _payload():
    return {
        "title": "Trip",
        "items": [
            {
                "spend_date": "2026-05-01",
                "category": "meals",
                "description": "Dinner",
                "amount": "42.00",
                "vat_amount": "0",
            }
        ],
    }


@pytest.mark.asyncio
async def test_owner_is_approver_by_default_and_appoints_others(auth_client, client):
    await _activate(auth_client)
    # The owner (first-registered) is an approver.
    me = (await auth_client.get("/api/v1/auth/me")).json()
    assert me["user"]["is_expense_approver"] is True

    tok, uid = await _member(auth_client, client, "finance@corp.io", "Finance")
    # A plain user is NOT an approver and cannot decide.
    emp_tok, _ = await _member(auth_client, client, "emp@corp.io")
    rid = (await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp_tok))).json()["id"]
    await _complete_and_submit(client, rid, _h(emp_tok))
    assert (
        await client.post(
            f"/api/v1/expenses/{rid}/decision",
            json={"action": "approve", "version": 1},
            headers=_h(tok),
        )
    ).status_code == 403

    # The owner appoints the finance user as an approver.
    up = await auth_client.patch(f"/api/v1/team/members/{uid}", json={"is_expense_approver": True})
    assert up.status_code == 200 and up.json()["is_expense_approver"] is True

    # Now that user can approve (and see the report).
    ok = await client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "approve", "version": 1},
        headers=_h(tok),
    )
    assert ok.status_code == 200 and ok.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_cannot_approve_own_report(auth_client, client):
    await _activate(auth_client)
    # Make a second approver so the owner can submit their own report.
    tok, uid = await _member(auth_client, client, "cfo@corp.io", "CFO")
    await auth_client.patch(f"/api/v1/team/members/{uid}", json={"is_expense_approver": True})

    # The owner (an approver) submits their own report → cannot self-approve.
    rid = (await auth_client.post("/api/v1/expenses", json=_payload())).json()["id"]
    await _complete_and_submit(auth_client, rid, {})
    mine = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision", json={"action": "approve", "version": 1}
    )
    assert mine.status_code == 403 and "own" in mine.json()["detail"]
    # But the other approver can.
    assert (
        await client.post(
            f"/api/v1/expenses/{rid}/decision",
            json={"action": "approve", "version": 1},
            headers=_h(tok),
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_cannot_remove_last_approver(auth_client, client):
    await _activate(auth_client)
    me = (await auth_client.get("/api/v1/auth/me")).json()
    # Removing the only approver (the owner) is refused.
    r = await auth_client.patch(
        f"/api/v1/team/members/{me['user']['id']}", json={"is_expense_approver": False}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bank_statement_verification(auth_client, client):
    await _activate(auth_client)
    emp_tok, _ = await _member(auth_client, client, "emp@corp.io")

    # Import a bank statement → an 'available' transaction in the inbox.
    csv = b"date,description,amount\n2026-05-01,Dinner Nobelhart,-42.00\n"
    imp = await client.post(
        "/api/v1/expenses/import/bank-statement",
        files={"file": ("stmt.csv", io.BytesIO(csv), "text/csv")},
        headers=_h(emp_tok),
    )
    assert imp.status_code == 200

    # A manually-typed item starts unverified.
    rid = (await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp_tok))).json()["id"]
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp_tok))).json()
    item = rep["items"][0]
    assert item["verified"] is False

    # The matching statement line is offered as a candidate…
    cands = (
        await client.get(
            f"/api/v1/expenses/{rid}/items/{item['id']}/match-candidates", headers=_h(emp_tok)
        )
    ).json()
    assert len(cands) == 1 and cands[0]["amount"] == "42.00"

    # …and matching it verifies the item.
    m = await client.post(
        f"/api/v1/expenses/{rid}/items/{item['id']}/match",
        json={"transaction_id": cands[0]["id"]},
        headers=_h(emp_tok),
    )
    assert m.status_code == 200
    it = m.json()["items"][0]
    assert it["verified"] is True and "Dinner" in it["bank_reference"]

    # The line is now consumed from the inbox.
    inbox = (await client.get("/api/v1/expenses/transactions", headers=_h(emp_tok))).json()
    assert all(t["id"] != cands[0]["id"] for t in inbox)

    # Unmatch returns it to the inbox and clears verification.
    um = await client.delete(
        f"/api/v1/expenses/{rid}/items/{item['id']}/match", headers=_h(emp_tok)
    )
    assert um.json()["items"][0]["verified"] is False
    inbox2 = (await client.get("/api/v1/expenses/transactions", headers=_h(emp_tok))).json()
    assert any(t["id"] == cands[0]["id"] for t in inbox2)


@pytest.mark.asyncio
async def test_match_rejects_amount_mismatch(auth_client, client):
    await _activate(auth_client)
    emp_tok, _ = await _member(auth_client, client, "emp@corp.io")
    csv = b"date,description,amount\n2026-05-01,Something else,-99.99\n"
    await client.post(
        "/api/v1/expenses/import/bank-statement",
        files={"file": ("s.csv", io.BytesIO(csv), "text/csv")},
        headers=_h(emp_tok),
    )
    rid = (await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp_tok))).json()["id"]
    item = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp_tok))).json()["items"][0]
    txn = (await client.get("/api/v1/expenses/transactions", headers=_h(emp_tok))).json()[0]
    # 42.00 item vs 99.99 line → refused, and no candidates offered.
    bad = await client.post(
        f"/api/v1/expenses/{rid}/items/{item['id']}/match",
        json={"transaction_id": txn["id"]},
        headers=_h(emp_tok),
    )
    assert bad.status_code == 422
    cands = (
        await client.get(
            f"/api/v1/expenses/{rid}/items/{item['id']}/match-candidates", headers=_h(emp_tok)
        )
    ).json()
    assert cands == []
