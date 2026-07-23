"""Issued-invoice DRAFT lifecycle (INV-3): draft → approve → issue (number
allocated only at issue), edit-only-while-draft immutability, duplicate, cancel,
dispute/undispute, bad-debt write-off, and the guards that a non-issued document
refuses payment / send / credit-note / PDF."""

import pytest

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}


def _draft_body(**over):
    body = {
        "buyer_name": "Globex SARL",
        "buyer_country": "FR",
        "issue_date": "2026-05-10",
        "due_date": "2026-06-09",  # 30-day term
        "draft": True,
        "lines": [
            {"description": "Consulting", "quantity": "2", "unit_price": "100.00", "vat_rate": "21"}
        ],
    }
    body.update(over)
    return body


async def _activate(auth_client):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


async def _new_draft(auth_client, **over):
    r = await auth_client.post("/api/v1/issued", json=_draft_body(**over))
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_draft_has_no_number_until_issued(auth_client):
    await _activate(auth_client)
    d = await _new_draft(auth_client)
    assert d["lifecycle"] == "draft"
    assert d["number"] is None
    assert d["status"] == "draft"
    assert d["total"] == "242.00"  # 2 x 100 x 1.21

    # Approve → still no number, but lifecycle advances.
    ap = await auth_client.post(f"/api/v1/issued/{d['id']}/approve")
    assert ap.status_code == 200, ap.text
    assert ap.json()["lifecycle"] == "approved"
    assert ap.json()["number"] is None

    # Issue → number is allocated now, lifecycle becomes issued.
    iss = await auth_client.post(f"/api/v1/issued/{d['id']}/issue", json={})
    assert iss.status_code == 200, iss.text
    body = iss.json()
    assert body["lifecycle"] == "issued"
    assert body["number"] is not None
    assert body["issued_at"] is not None


@pytest.mark.asyncio
async def test_issue_allocates_gap_free_sequential_numbers(auth_client):
    await _activate(auth_client)
    d1 = await _new_draft(auth_client)
    d2 = await _new_draft(auth_client)
    # Issue the SECOND draft first — numbering follows issue order, not create order.
    n2 = (await auth_client.post(f"/api/v1/issued/{d2['id']}/issue", json={})).json()["number"]
    n1 = (await auth_client.post(f"/api/v1/issued/{d1['id']}/issue", json={})).json()["number"]
    assert n2 != n1
    # Sequential 4-digit suffix, no gaps.
    s1 = int(n1.rsplit("-", 1)[1])
    s2 = int(n2.rsplit("-", 1)[1])
    assert abs(s1 - s2) == 1


@pytest.mark.asyncio
async def test_edit_only_while_draft(auth_client):
    await _activate(auth_client)
    d = await _new_draft(auth_client)
    # Edit the draft: change the line quantity → totals recompute server-side.
    edit = await auth_client.patch(
        f"/api/v1/issued/{d['id']}",
        json=_draft_body(
            lines=[
                {
                    "description": "Consulting",
                    "quantity": "3",
                    "unit_price": "100.00",
                    "vat_rate": "21",
                }
            ]
        ),
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["total"] == "363.00"  # 3 x 100 x 1.21
    # The line set is actually replaced (persisted), not just the scalar totals.
    got = (await auth_client.get(f"/api/v1/issued/{d['id']}")).json()
    assert len(got["lines"]) == 1
    assert got["lines"][0]["quantity"] == "3.000"
    assert got["lines"][0]["net_amount"] == "300.00"

    # Issue it, then editing is refused (an issued invoice is immutable).
    await auth_client.post(f"/api/v1/issued/{d['id']}/issue", json={})
    late = await auth_client.patch(f"/api/v1/issued/{d['id']}", json=_draft_body())
    assert late.status_code == 409


@pytest.mark.asyncio
async def test_non_issued_document_refuses_payment_send_credit_pdf(auth_client):
    await _activate(auth_client)
    d = await _new_draft(auth_client)
    iid = d["id"]
    # No number → no PDF/XML.
    assert (await auth_client.get(f"/api/v1/issued/{iid}/pdf")).status_code == 409
    assert (await auth_client.get(f"/api/v1/issued/{iid}/xml")).status_code == 409
    # Not a receivable yet.
    assert (
        await auth_client.patch(f"/api/v1/issued/{iid}/payment", json={"amount_paid": "1.00"})
    ).status_code == 409
    assert (
        await auth_client.post(f"/api/v1/issued/{iid}/send", json={"to_email": "x@y.io"})
    ).status_code == 409
    assert (await auth_client.post(f"/api/v1/issued/{iid}/credit-note", json={})).status_code == 409


@pytest.mark.asyncio
async def test_duplicate_creates_fresh_draft(auth_client):
    await _activate(auth_client)
    d = await _new_draft(auth_client)
    src = (await auth_client.post(f"/api/v1/issued/{d['id']}/issue", json={})).json()
    dup = await auth_client.post(f"/api/v1/issued/{src['id']}/duplicate")
    assert dup.status_code == 201, dup.text
    body = dup.json()
    assert body["id"] != src["id"]
    assert body["lifecycle"] == "draft"
    assert body["number"] is None
    assert body["buyer_name"] == src["buyer_name"]
    assert body["total"] == src["total"]


@pytest.mark.asyncio
async def test_cancel_draft(auth_client):
    await _activate(auth_client)
    d = await _new_draft(auth_client)
    c = await auth_client.post(f"/api/v1/issued/{d['id']}/cancel")
    assert c.status_code == 200, c.text
    assert c.json()["lifecycle"] == "cancelled"
    assert c.json()["status"] == "void"
    # Can't cancel again (it's no longer a draft/approved).
    assert (await auth_client.post(f"/api/v1/issued/{d['id']}/cancel")).status_code == 409
    # An issued invoice cannot be cancelled (must be voided).
    d2 = await _new_draft(auth_client)
    await auth_client.post(f"/api/v1/issued/{d2['id']}/issue", json={})
    assert (await auth_client.post(f"/api/v1/issued/{d2['id']}/cancel")).status_code == 409


@pytest.mark.asyncio
async def test_dispute_then_undispute(auth_client):
    await _activate(auth_client)
    d = await _new_draft(auth_client)
    iid = (await auth_client.post(f"/api/v1/issued/{d['id']}/issue", json={})).json()["id"]

    disp = await auth_client.post(f"/api/v1/issued/{iid}/dispute", json={"reason": "wrong amount"})
    assert disp.status_code == 200, disp.text
    assert disp.json()["lifecycle"] == "disputed"
    assert disp.json()["status"] == "disputed"
    assert disp.json()["dispute_reason"] == "wrong amount"
    # A disputed invoice is still a receivable — a payment is still accepted.
    assert (
        await auth_client.patch(f"/api/v1/issued/{iid}/payment", json={"amount_paid": "10.00"})
    ).status_code == 200

    un = await auth_client.post(f"/api/v1/issued/{iid}/undispute")
    assert un.status_code == 200
    assert un.json()["lifecycle"] == "issued"
    # Can't dispute a draft (must be issued first).
    d2 = await _new_draft(auth_client)
    assert (
        await auth_client.post(f"/api/v1/issued/{d2['id']}/dispute", json={})
    ).status_code == 409


@pytest.mark.asyncio
async def test_write_off_bad_debt(auth_client):
    await _activate(auth_client)
    d = await _new_draft(auth_client)
    iid = (await auth_client.post(f"/api/v1/issued/{d['id']}/issue", json={})).json()["id"]

    wo = await auth_client.post(f"/api/v1/issued/{iid}/write-off", json={"reason": "insolvent"})
    assert wo.status_code == 200, wo.text
    assert wo.json()["lifecycle"] == "written_off"
    assert wo.json()["status"] == "written_off"
    # A written-off invoice owes nothing and refuses new payments.
    assert wo.json()["outstanding"] == "0.00"
    assert (
        await auth_client.patch(f"/api/v1/issued/{iid}/payment", json={"amount_paid": "1.00"})
    ).status_code == 409


@pytest.mark.asyncio
async def test_reports_exclude_drafts(auth_client):
    await _activate(auth_client)
    # One issued invoice + one draft (never issued). Only the issued one counts.
    d_issue = await _new_draft(auth_client)
    await auth_client.post(f"/api/v1/issued/{d_issue['id']}/issue", json={})
    await _new_draft(auth_client)  # left as a draft

    summ = (await auth_client.get("/api/v1/issued/reports/summary")).json()
    assert summ["count"] == 1  # the draft is not turnover
    rec = (await auth_client.get("/api/v1/issued/reports/receivables")).json()
    total = sum(b["count"] for b in rec["statuses"])
    assert total == 1  # only the issued invoice is a receivable


@pytest.mark.asyncio
async def test_authz_draft_actions_require_write(auth_client, client):
    await _activate(auth_client)
    d = await _new_draft(auth_client)
    # A read-only member cannot approve/issue/dispute.
    inv = await auth_client.post(
        "/api/v1/team/invites", json={"email": "ro@acme.io", "role": "user"}
    )
    tok = (
        await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": inv.json()["token"], "name": "RO", "password": "supersecret"},
        )
    ).json()["token"]["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert (await client.post(f"/api/v1/issued/{d['id']}/approve", headers=h)).status_code == 403
    assert (
        await client.post(f"/api/v1/issued/{d['id']}/issue", json={}, headers=h)
    ).status_code == 403
