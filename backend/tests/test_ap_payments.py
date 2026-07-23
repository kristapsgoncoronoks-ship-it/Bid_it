"""AP supplier-invoice payment ledger (Phase 13): record a payment against an
approved/scheduled invoice (partial → partially_paid, full → paid + legacy status),
the ledger history, overpay refused, paying a non-payable invoice refused, and the
PAYMENT_* authz split. The invoice is driven to `scheduled_for_payment` through the
real submit→approve→schedule path (Phase 08)."""

import pytest


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _member(auth_client, client, email, role="admin"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _make_invoice(auth_client, number="INV-100", unit_price="100"):
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Acme Supplies",
            "invoice_number": number,
            "issue_date": "2026-05-01",
            "due_date": "2026-12-01",  # future — so a partial payment reads "partial", not "overdue"
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Widgets",
                    "quantity": "1",
                    "unit_price": unit_price,
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _schedule(auth_client, client, iid):
    """Drive a fresh invoice draft → scheduled_for_payment via the real workflow."""
    approver = await _member(auth_client, client, f"appr-{iid[:8]}@acme.io", role="admin")
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    ver = sub.json()["version"]
    appr = await client.post(
        f"/api/v1/invoices/{iid}/approve", headers=_h(approver), json={"version": ver}
    )
    body = appr.json()
    sch = await auth_client.post(
        f"/api/v1/invoices/{iid}/transition",
        json={"version": body["version"], "target": "scheduled_for_payment"},
    )
    assert sch.status_code == 200, sch.text
    return sch.json()


@pytest.mark.asyncio
async def test_partial_then_full_payment(auth_client, client):
    iid = await _make_invoice(auth_client)  # total 100.00
    await _schedule(auth_client, client, iid)

    # A partial payment → partially_paid, outstanding 60.
    p1 = await auth_client.patch(
        f"/api/v1/invoices/{iid}/payment", json={"amount_paid": "40.00", "paid_date": "2026-05-20"}
    )
    assert p1.status_code == 200, p1.text
    b1 = p1.json()
    assert b1["workflow_state"] == "partially_paid"
    assert b1["amount_paid"] == "40.00" and b1["outstanding"] == "60.00"
    assert b1["payment_status"] == "partial"

    # Settle the rest → paid, outstanding 0, legacy aging status synced.
    p2 = await auth_client.patch(f"/api/v1/invoices/{iid}/payment", json={"amount_paid": "100.00"})
    assert p2.status_code == 200, p2.text
    b2 = p2.json()
    assert b2["workflow_state"] == "paid" and b2["status"] == "paid"
    assert b2["outstanding"] == "0.00" and b2["payment_status"] == "paid"
    assert b2["paid_date"] is not None

    # The ledger records both deltas (40, then 60).
    ledger = (await auth_client.get(f"/api/v1/invoices/{iid}/payments")).json()
    assert [e["amount"] for e in ledger] == ["40.00", "60.00"]


@pytest.mark.asyncio
async def test_full_payment_in_one_shot(auth_client, client):
    iid = await _make_invoice(auth_client)
    await _schedule(auth_client, client, iid)
    p = await auth_client.patch(f"/api/v1/invoices/{iid}/payment", json={"amount_paid": "100.00"})
    assert p.status_code == 200 and p.json()["workflow_state"] == "paid"


@pytest.mark.asyncio
async def test_overpayment_refused(auth_client, client):
    iid = await _make_invoice(auth_client)
    await _schedule(auth_client, client, iid)
    p = await auth_client.patch(f"/api/v1/invoices/{iid}/payment", json={"amount_paid": "150.00"})
    assert p.status_code == 400 and "exceeds the invoice total" in p.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_pay_unscheduled_invoice(auth_client):
    # A brand-new draft invoice is not payable.
    iid = await _make_invoice(auth_client)
    p = await auth_client.patch(f"/api/v1/invoices/{iid}/payment", json={"amount_paid": "10.00"})
    assert p.status_code == 422 and "scheduled for payment" in p.json()["detail"]


@pytest.mark.asyncio
async def test_payment_authz_split(auth_client, client):
    # authz.require runs before the payable-state check, so a draft invoice is
    # enough to prove the gate (and avoids the member seat cap _schedule would use).
    iid = await _make_invoice(auth_client)

    # EMPLOYEE (role 'user') → no PAYMENT_READ/WRITE.
    emp = await _member(auth_client, client, "emp@corp.io", role="user")
    assert (
        await client.get(f"/api/v1/invoices/{iid}/payments", headers=_h(emp))
    ).status_code == 403

    # READ_ONLY (role 'user_free') can read the ledger but not record a payment.
    ro = await _member(auth_client, client, "ro@corp.io", role="user_free")
    assert (await client.get(f"/api/v1/invoices/{iid}/payments", headers=_h(ro))).status_code == 200
    assert (
        await client.patch(
            f"/api/v1/invoices/{iid}/payment", headers=_h(ro), json={"amount_paid": "10.00"}
        )
    ).status_code == 403
