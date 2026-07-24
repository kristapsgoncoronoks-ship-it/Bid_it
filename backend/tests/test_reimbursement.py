"""Expense reimbursement payout batches (Phase 09): group approved reports, pay
them together (→ reimbursed + payment metadata), export a bank/payroll CSV, with
authorization + tenant isolation + optimistic concurrency."""

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


async def _member(auth_client, client, email, role="user", name="Employee"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": name, "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


def _payload(title="Trip", amount="300.00"):
    return {
        "title": title,
        "currency": "EUR",
        "items": [
            {
                "spend_date": "2026-05-01",
                "category": "travel",
                "description": "Flight",
                "amount": amount,
                "vat_amount": "0",
            }
        ],
    }


async def _draft(client, emp, title="Trip", amount="300.00"):
    r = await client.post("/api/v1/expenses", json=_payload(title, amount), headers=_h(emp))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _complete(client, rid, emp):
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp))).json()
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Client meeting"},
            headers=_h(emp),
        )
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("receipt.png", _PNG, "image/png")},
            headers=_h(emp),
        )


async def _approved(auth_client, client, emp, title="Trip", amount="300.00"):
    """A fully approved report ready for reimbursement."""
    rid = await _draft(client, emp, title, amount)
    await _complete(client, rid, emp)
    assert (await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))).status_code == 200
    dec = await auth_client.post(f"/api/v1/expenses/{rid}/decision", json={"action": "approve"})
    assert dec.status_code == 200 and dec.json()["status"] == "approved"
    return rid


@pytest.mark.asyncio
async def test_batch_pay_marks_reports_reimbursed(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e1@corp.io")
    r1 = await _approved(auth_client, client, emp, "A", "100.00")
    r2 = await _approved(auth_client, client, emp, "B", "200.00")

    # Owner (an approver) groups both into a payout batch.
    batch = await auth_client.post(
        "/api/v1/reimbursements", json={"report_ids": [r1, r2], "method": "bank_transfer"}
    )
    assert batch.status_code == 201, batch.text
    b = batch.json()
    assert b["status"] == "open" and b["report_count"] == 2 and b["total_eur"] == "300.00"

    # Pay it → both reports become reimbursed with the reference stamped.
    paid = await auth_client.post(
        f"/api/v1/reimbursements/{b['id']}/pay",
        json={"version": b["version"], "reference": "PAY-2026-001"},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"
    for rep in paid.json()["reports"]:
        assert rep["status"] == "reimbursed" and rep["payment_reference"] == "PAY-2026-001"

    # And it's reflected on the report itself.
    got = (await auth_client.get(f"/api/v1/expenses/{r1}")).json()
    assert got["status"] == "reimbursed"


@pytest.mark.asyncio
async def test_only_approved_reports_can_be_batched(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e2@corp.io")
    draft = await _draft(client, emp)  # still draft
    r = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [draft]})
    assert r.status_code == 422 and "approved" in r.json()["detail"]


@pytest.mark.asyncio
async def test_report_cannot_be_in_two_batches(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e3@corp.io")
    rid = await _approved(auth_client, client, emp)
    first = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert first.status_code == 201
    again = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert again.status_code == 422 and "already in a payout batch" in again.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_open_batch_unlinks_reports(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e4@corp.io")
    rid = await _approved(auth_client, client, emp)
    b = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})).json()
    cancel = await auth_client.delete(f"/api/v1/reimbursements/{b['id']}")
    assert cancel.status_code == 204
    # The report is payable again (approved, unbatched) → can go into a new batch.
    b2 = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert b2.status_code == 201


@pytest.mark.asyncio
async def test_pay_is_version_guarded(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e5@corp.io")
    rid = await _approved(auth_client, client, emp)
    b = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})).json()
    stale = await auth_client.post(
        f"/api/v1/reimbursements/{b['id']}/pay", json={"version": 999, "reference": "X"}
    )
    assert stale.status_code == 409
    # A paid batch can't be paid again.
    ok = await auth_client.post(
        f"/api/v1/reimbursements/{b['id']}/pay", json={"version": b["version"]}
    )
    assert ok.status_code == 200
    twice = await auth_client.post(
        f"/api/v1/reimbursements/{b['id']}/pay", json={"version": ok.json()["version"]}
    )
    assert twice.status_code == 409


@pytest.mark.asyncio
async def test_export_csv(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e6@corp.io")
    rid = await _approved(auth_client, client, emp, "Berlin", "150.00")
    b = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})).json()
    exp = await auth_client.get(f"/api/v1/reimbursements/{b['id']}/export")
    assert exp.status_code == 200
    assert "text/csv" in exp.headers["content-type"]
    body = exp.text
    assert "Employee" in body and "Berlin" in body and "150.00" in body


@pytest.mark.asyncio
async def test_non_approver_cannot_run_payouts(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e7@corp.io")
    rid = await _approved(auth_client, client, emp)
    # The employee (role user, not an approver) cannot create a payout batch.
    r = await client.post("/api/v1/reimbursements", headers=_h(emp), json={"report_ids": [rid]})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_cross_tenant_isolation(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e8@corp.io")
    rid = await _approved(auth_client, client, emp)
    b = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})).json()

    # A second, unrelated org (owner is an approver; enable its module).
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other",
            "name": "Other",
            "email": "other@other.io",
            "password": "supersecret",
        },
    )
    other = reg.json()["token"]["access_token"]
    await client.put("/api/v1/modules/expenses", json={"enabled": True}, headers=_h(other))
    # Can't see our batch, and can't batch our report.
    assert (
        await client.get(f"/api/v1/reimbursements/{b['id']}", headers=_h(other))
    ).status_code == 404
    steal = await client.post(
        "/api/v1/reimbursements", headers=_h(other), json={"report_ids": [rid]}
    )
    assert steal.status_code == 422  # report not found in the other org
