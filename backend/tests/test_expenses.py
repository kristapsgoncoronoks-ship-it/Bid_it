"""Employee expense reports: workflow, per-employee ownership, VAT/EUR, PDF."""
import pytest


def _payload(title="Berlin trip", ccy="EUR"):
    return {
        "title": title, "currency": ccy,
        "items": [
            {"spend_date": "2026-05-01", "category": "travel", "description": "Flight", "amount": "300.00", "vat_amount": "0"},
            {"spend_date": "2026-05-02", "category": "meals", "description": "Dinner", "amount": "45.00", "vat_amount": "7.50"},
        ],
    }


async def _activate(auth_client):
    r = await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert r.status_code == 200, r.text


async def _member(auth_client, client, email, name="Employee"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": "user"})
    token = inv.json()["token"]
    acc = await client.post("/api/v1/auth/accept-invite", json={"token": token, "name": name, "password": "supersecret"})
    return acc.json()["token"]["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


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
    await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))

    # employee cannot approve their own
    assert (await client.post(f"/api/v1/expenses/{rid}/decision", json={"action": "approve"}, headers=_h(emp))).status_code == 403

    # manager approves, then reimburses
    ap = await auth_client.post(f"/api/v1/expenses/{rid}/decision", json={"action": "approve", "note": "ok"})
    assert ap.status_code == 200 and ap.json()["status"] == "approved"
    assert ap.json()["decided_by"] == "owner@acme.io"

    # can't reimburse before approve is a no-op here (already approved) → reimburse works
    rb = await auth_client.post(f"/api/v1/expenses/{rid}/decision", json={"action": "reimburse"})
    assert rb.status_code == 200 and rb.json()["status"] == "reimbursed"

    # can't approve an already-decided report
    bad = await auth_client.post(f"/api/v1/expenses/{rid}/decision", json={"action": "approve"})
    assert bad.status_code == 409


@pytest.mark.asyncio
async def test_foreign_currency_eur_and_summary(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp@corp.io")
    rid = (await client.post("/api/v1/expenses", json=_payload("US trip", "USD"), headers=_h(emp))).json()["id"]
    sub = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert sub.json()["total_eur"] is not None
    assert float(sub.json()["total_eur"]) < 345.0  # USD → fewer EUR

    s = (await client.get("/api/v1/expenses/summary", headers=_h(emp))).json()
    assert s["my_submitted"] == 1
    assert s["reclaimable_vat"] == "7.50"
    cats = {c["category"]: c["total"] for c in s["by_category"]}
    assert cats["travel"] == "300.00" and cats["meals"] == "45.00"


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
