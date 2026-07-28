"""Employee expense reports: workflow, per-employee ownership, VAT/EUR, PDF."""

import pytest


def _payload(title="Berlin trip", ccy="EUR"):
    return {
        "title": title,
        "currency": ccy,
        "items": [
            {
                "spend_date": "2026-05-01",
                "category": "travel",
                "description": "Flight",
                "amount": "300.00",
                "vat_amount": "0",
            },
            {
                "spend_date": "2026-05-02",
                "category": "meals",
                "description": "Dinner",
                "amount": "45.00",
                "vat_amount": "7.50",
            },
        ],
    }


async def _activate(auth_client):
    r = await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert r.status_code == 200, r.text


async def _member(auth_client, client, email, name="Employee"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": "user"})
    token = inv.json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite", json={"token": token, "name": name, "password": "supersecret"}
    )
    return acc.json()["token"]["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# A minimal valid 1x1 PNG, used as a receipt document in tests.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def _complete(client, rid, headers):
    """Give every item a business purpose + attached receipt so the report can be
    submitted (the compliance gate requires both on every entry)."""
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=headers)).json()
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Client meeting"},
            headers=headers,
        )
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("receipt.png", _PNG, "image/png")},
            headers=headers,
        )


@pytest.mark.asyncio
async def test_module_gated(auth_client):
    r = await auth_client.post("/api/v1/expenses", json=_payload())
    assert r.status_code == 403  # module off by default


@pytest.mark.asyncio
async def test_create_totals_and_submit(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")

    r = await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp))
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "draft"
    assert rep["total"] == "345.00"
    assert rep["vat_total"] == "7.50"
    assert rep["employee_name"] == "Employee"

    await _complete(client, rep["id"], _h(emp))
    sub = await client.post(f"/api/v1/expenses/{rep['id']}/submit", headers=_h(emp))
    assert sub.status_code == 200
    assert sub.json()["status"] == "submitted"
    assert sub.json()["total_eur"] == "345.00"  # EUR 1:1


@pytest.mark.asyncio
async def test_employee_ownership_isolation(auth_client, client):
    await _activate(auth_client)
    emp1 = await _member(auth_client, client, "emp1@corp.io")
    emp2 = await _member(auth_client, client, "emp2@corp.io")

    made = await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp1))
    rid = made.json()["id"]

    # emp2 can't see emp1's report, and their own list is empty
    assert (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp2))).status_code == 404
    assert (await client.get("/api/v1/expenses", headers=_h(emp2))).json()["total"] == 0
    # emp1 sees their own
    assert (await client.get("/api/v1/expenses", headers=_h(emp1))).json()["total"] == 1
    # the owner (manager) sees all reports in the tenant
    assert (await auth_client.get("/api/v1/expenses")).json()["total"] == 1


@pytest.mark.asyncio
async def test_approval_workflow(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    rid = (await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp))).json()["id"]
    await _complete(client, rid, _h(emp))
    await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))

    # employee cannot approve their own
    assert (
        await client.post(
            f"/api/v1/expenses/{rid}/decision",
            json={"action": "approve", "version": 1},
            headers=_h(emp),
        )
    ).status_code == 403

    # manager approves, then reimburses
    ap = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "approve", "note": "ok", "version": 1},
    )
    assert ap.status_code == 200 and ap.json()["status"] == "approved"
    assert ap.json()["decided_by"] == "owner@acme.io"

    # can't reimburse before approve is a no-op here (already approved) → reimburse works
    rb = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "reimburse", "version": ap.json()["version"]},
    )
    assert rb.status_code == 200 and rb.json()["status"] == "reimbursed"

    # can't approve an already-decided report
    bad = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "approve", "version": rb.json()["version"]},
    )
    assert bad.status_code == 409


@pytest.mark.asyncio
async def test_submit_requires_business_purpose_and_receipt(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    rep = (await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp))).json()
    rid, item0 = rep["id"], rep["items"][0]["id"]

    # No business purpose, no receipt → blocked (item needs both).
    r1 = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert r1.status_code == 422
    assert "needs business purpose, receipt" in r1.json()["detail"]

    # Add business purposes on both items; still missing receipts → blocked.
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Q2 client visit"},
            headers=_h(emp),
        )
    r2 = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert r2.status_code == 422
    assert (
        "needs receipt" in r2.json()["detail"]
        and "needs business purpose" not in r2.json()["detail"]
    )

    # Attach receipts on both → now it submits.
    for it in rep["items"]:
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("r.png", _PNG, "image/png")},
            headers=_h(emp),
        )
    ok = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert ok.status_code == 200, ok.text

    # The saved business purpose is reflected on the item.
    got = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp))).json()
    assert got["items"][0]["comment"] == "Q2 client visit"
    assert got["items"][0]["has_receipt"] is True
    assert item0  # sanity


@pytest.mark.asyncio
async def test_update_item_only_owner_and_draft(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    other = await _member(auth_client, client, "other@corp.io")
    rep = (await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp))).json()
    rid, iid = rep["id"], rep["items"][0]["id"]

    # A different employee can't edit this item (not their report).
    assert (
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{iid}", json={"comment": "x"}, headers=_h(other)
        )
    ).status_code == 403

    # Owner edits business purpose + category.
    patched = await client.patch(
        f"/api/v1/expenses/{rid}/items/{iid}",
        json={"comment": "Sales trip", "category": "accommodation"},
        headers=_h(emp),
    )
    assert patched.status_code == 200
    it = next(i for i in patched.json()["items"] if i["id"] == iid)
    assert it["comment"] == "Sales trip" and it["category"] == "accommodation"


@pytest.mark.asyncio
async def test_foreign_currency_eur_and_summary(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    rid = (
        await client.post("/api/v1/expenses", json=_payload("US trip", "USD"), headers=_h(emp))
    ).json()["id"]
    await _complete(client, rid, _h(emp))
    sub = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert sub.json()["total_eur"] is not None
    assert float(sub.json()["total_eur"]) < 345.0  # USD → fewer EUR

    s = (await client.get("/api/v1/expenses/summary", headers=_h(emp))).json()
    assert s["my_submitted"] == 1
    assert s["reclaimable_vat"] == "7.50"
    cats = {c["category"]: c["total"] for c in s["by_category"]}
    assert cats["travel"] == "300.00" and cats["meals"] == "45.00"


@pytest.mark.asyncio
async def test_reclaimable_vat_excludes_non_reclaimable_items(auth_client, client):
    # C1.8: a non-reclaimable line's VAT (paid, but not claimable — e.g. client
    # entertainment) must not inflate the "Reclaimable VAT" figure.
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    payload = _payload()
    payload["items"].append(
        {
            "spend_date": "2026-05-03",
            "category": "meals",
            "description": "Client entertainment",
            "amount": "100.00",
            "vat_amount": "12.00",
            "reclaimable_tax": False,
        }
    )
    rid = (await client.post("/api/v1/expenses", json=payload, headers=_h(emp))).json()["id"]
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp))).json()
    assert rep["vat_total"] == "19.50"  # 7.50 + 12.00 — the total tax paid is unchanged

    await _complete(client, rid, _h(emp))
    await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))

    s = (await client.get("/api/v1/expenses/summary", headers=_h(emp))).json()
    assert s["reclaimable_vat"] == "7.50"  # only the reclaimable line, not 19.50


@pytest.mark.asyncio
async def test_reclaimable_vat_excludes_draft_and_rejected_reports(auth_client, client):
    # C1.8: a draft's figures aren't final and a rejected report was never
    # approved for reimbursement — neither belongs in "cash we can reclaim."
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")

    submitted_rid = (
        await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp))
    ).json()["id"]
    await _complete(client, submitted_rid, _h(emp))
    await client.post(f"/api/v1/expenses/{submitted_rid}/submit", headers=_h(emp))

    # a draft report — never submitted, still fully reclaimable-looking VAT
    await client.post("/api/v1/expenses", json=_payload("Draft trip"), headers=_h(emp))

    # a rejected report — submitted, then explicitly refused
    rejected_rid = (
        await client.post("/api/v1/expenses", json=_payload("Refused trip"), headers=_h(emp))
    ).json()["id"]
    await _complete(client, rejected_rid, _h(emp))
    sub = await client.post(f"/api/v1/expenses/{rejected_rid}/submit", headers=_h(emp))
    await auth_client.post(
        f"/api/v1/expenses/{rejected_rid}/decision",
        json={
            "action": "reject",
            "note": "no receipt policy match",
            "version": sub.json()["version"],
        },
    )

    s = (await client.get("/api/v1/expenses/summary", headers=_h(emp))).json()
    assert s["my_draft"] == 1
    # only the submitted report's 7.50 counts — the draft's and the rejected
    # report's VAT (each 7.50) never enter the sum, not even partially.
    assert s["reclaimable_vat"] == "7.50"


@pytest.mark.asyncio
async def test_pdf_export(auth_client, client):
    pytest.importorskip("reportlab")
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    rid = (await client.post("/api/v1/expenses", json=_payload(), headers=_h(emp))).json()["id"]
    pdf = await client.get(f"/api/v1/expenses/{rid}/pdf", headers=_h(emp))
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_pdf_export_with_unicode_title(auth_client, client):
    # A title with non-latin-1 characters (em dash) must not break the
    # Content-Disposition header — an ASCII filename + RFC 5987 filename* are set.
    pytest.importorskip("reportlab")
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp2@corp.io")
    rid = (
        await client.post(
            "/api/v1/expenses", json=_payload(title="Berlin sales trip — March"), headers=_h(emp)
        )
    ).json()["id"]
    pdf = await client.get(f"/api/v1/expenses/{rid}/pdf", headers=_h(emp))
    assert pdf.status_code == 200
    assert pdf.content[:5] == b"%PDF-"
    cd = pdf.headers["content-disposition"]
    assert "filename=" in cd and "filename*=UTF-8''" in cd
