"""SAP Concur-style expenses: inbox transactions → report entries, receipts,
per-entry comments, and a report comment thread."""

import io

import pytest


async def _activate(auth_client):
    r = await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert r.status_code == 200


async def _member(auth_client, client, email, name="Employee"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": "user"})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": name, "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _import(client, token):
    csv = "Date,Description,Amount\n2026-05-01,AWS,-120.00\n2026-05-05,Hotel,-89.50\n"
    files = {"file": ("s.csv", io.BytesIO(csv.encode()), "text/csv")}
    return (
        await client.post("/api/v1/expenses/import/bank-statement", files=files, headers=_h(token))
    ).json()


@pytest.mark.asyncio
async def test_report_from_inbox_transactions(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    imported = await _import(client, emp)
    ids = [t["id"] for t in imported["transactions"]]

    # Build a report directly from selected inbox transactions.
    r = await client.post(
        "/api/v1/expenses", json={"title": "May card", "transaction_ids": ids}, headers=_h(emp)
    )
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["total"] == "209.50"  # 120 + 89.50
    assert len(rep["items"]) == 2
    assert rep["items"][0]["payment_method"] == "company_card"

    # Those transactions are now assigned → inbox is empty.
    assert (await client.get("/api/v1/expenses/transactions", headers=_h(emp))).json() == []
    # ...and can't be reused.
    dup = await client.post(
        "/api/v1/expenses", json={"title": "again", "transaction_ids": ids}, headers=_h(emp)
    )
    assert dup.status_code == 404


@pytest.mark.asyncio
async def test_add_transaction_to_existing_draft(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    imported = await _import(client, emp)
    tid = imported["transactions"][0]["id"]

    rid = (await client.post("/api/v1/expenses", json={"title": "Trip"}, headers=_h(emp))).json()[
        "id"
    ]
    r = await client.post(
        f"/api/v1/expenses/{rid}/items/from-transaction",
        json={"transaction_id": tid, "category": "software"},
        headers=_h(emp),
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["items"]) == 1
    assert d["items"][0]["category"] == "software"
    assert d["total"] == "120.00"


@pytest.mark.asyncio
async def test_item_comment_and_receipt(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    # create a report with an item carrying a per-entry comment
    r = await client.post(
        "/api/v1/expenses",
        json={
            "title": "Trip",
            "items": [
                {
                    "spend_date": "2026-05-01",
                    "category": "meals",
                    "description": "Dinner",
                    "amount": "42.00",
                    "vat_amount": "0",
                    "comment": "Client dinner, 3 people",
                }
            ],
        },
        headers=_h(emp),
    )
    rep = r.json()
    item = rep["items"][0]
    assert item["comment"] == "Client dinner, 3 people"
    assert item["has_receipt"] is False

    # attach a receipt document (PDF) to the entry
    files = {"file": ("receipt.pdf", io.BytesIO(b"%PDF-1.4 receipt"), "application/pdf")}
    up = await client.post(
        f"/api/v1/expenses/{rep['id']}/items/{item['id']}/receipt", files=files, headers=_h(emp)
    )
    assert up.status_code == 200
    assert up.json()["items"][0]["has_receipt"] is True


@pytest.mark.asyncio
async def test_comment_thread_between_employee_and_manager(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    rep = (
        await client.post(
            "/api/v1/expenses",
            json={
                "title": "Trip",
                "items": [
                    {
                        "spend_date": "2026-05-01",
                        "category": "meals",
                        "description": "Dinner",
                        "amount": "42.00",
                        "vat_amount": "0",
                        "comment": "Team dinner",
                    }
                ],
            },
            headers=_h(emp),
        )
    ).json()
    rid, iid = rep["id"], rep["items"][0]["id"]
    # Business purpose set above; attach a receipt so the report can be submitted.
    await client.post(
        f"/api/v1/expenses/{rid}/items/{iid}/receipt",
        files={"file": ("r.pdf", io.BytesIO(b"%PDF-1.4 receipt"), "application/pdf")},
        headers=_h(emp),
    )
    sub = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert sub.status_code == 200, sub.text

    # employee comments
    c1 = await client.post(
        f"/api/v1/expenses/{rid}/comments",
        json={"body": "Receipt attached, please approve"},
        headers=_h(emp),
    )
    assert c1.status_code == 201
    # manager (owner) comments on the same thread
    c2 = await auth_client.post(
        f"/api/v1/expenses/{rid}/comments", json={"body": "Approved — thanks"}
    )
    assert c2.status_code == 201

    thread = (await client.get(f"/api/v1/expenses/{rid}/comments", headers=_h(emp))).json()
    assert [c["body"] for c in thread] == ["Receipt attached, please approve", "Approved — thanks"]
    assert thread[1]["author_name"] == "Owner"

    # a different employee cannot read this report's thread
    other = await _member(auth_client, client, "other@corp.io")
    assert (
        await client.get(f"/api/v1/expenses/{rid}/comments", headers=_h(other))
    ).status_code == 404
