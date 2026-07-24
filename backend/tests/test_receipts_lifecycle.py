"""Receipts cash-application (Phase 11): the PAYMENT_* authz split on the receipts
routes, reversing an allocation (a negative offsetting ledger entry that frees the
receipt and lowers the invoice), the double-reverse guard, that the ledger stays
healthy after a reversal, and the aging CSV export."""

import pytest
from sqlalchemy import select

from app.models.issued_invoice import IssuedInvoice

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


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _activate(auth_client):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


async def _invoice(auth_client, price="1000.00"):
    body = {
        "buyer_name": "Acme",
        "issue_date": "2026-01-10",
        "due_date": "2026-02-10",
        "vat_scheme": "standard",
        "lines": [{"description": "S", "quantity": "1", "unit_price": price, "vat_rate": "21"}],
    }
    return (await auth_client.post("/api/v1/issued", json=body)).json()


async def _receipt(auth_client, amount):
    return (
        await auth_client.post(
            "/api/v1/receipts", json={"amount": amount, "received_on": "2026-02-05"}
        )
    ).json()


async def _member(auth_client, client, email, role="user"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _paid(db_session, inv_id):
    row = await db_session.scalar(select(IssuedInvoice).where(IssuedInvoice.id == inv_id))
    return row.amount_paid


@pytest.mark.asyncio
async def test_reverse_allocation_frees_receipt_and_lowers_invoice(auth_client, db_session):
    await _activate(auth_client)
    inv = await _invoice(auth_client)  # total 1210
    rec = await _receipt(auth_client, "1210.00")
    detail = (
        await auth_client.post(
            f"/api/v1/receipts/{rec['id']}/allocate",
            json={"invoice_id": inv["id"], "amount": "1210.00"},
        )
    ).json()
    assert detail["unallocated"] == "0.00"
    assert await _paid(db_session, inv["id"]) == 1210.00
    entry_id = detail["allocations"][0]["id"]

    # Reverse it: receipt is free again, invoice back to unpaid.
    rev = await auth_client.post(
        f"/api/v1/receipts/{rec['id']}/deallocate", json={"payment_id": entry_id}
    )
    assert rev.status_code == 200, rev.text
    body = rev.json()
    assert body["unallocated"] == "1210.00" and body["allocated"] == "0.00"
    db_session.expire_all()
    assert await _paid(db_session, inv["id"]) == 0.00
    # The ledger keeps the original + a negative reversal row (append-only, auditable).
    amounts = sorted(a["amount"] for a in body["allocations"])
    assert amounts == ["-1210.00", "1210.00"]

    # The invoice's outstanding is back to the full amount → it can be re-applied.
    inv_now = (await auth_client.get(f"/api/v1/issued/{inv['id']}")).json()
    assert inv_now["outstanding"] == "1210.00" and inv_now["status"] != "paid"


@pytest.mark.asyncio
async def test_double_reverse_is_refused(auth_client):
    await _activate(auth_client)
    inv = await _invoice(auth_client)
    rec = await _receipt(auth_client, "1210.00")
    detail = (
        await auth_client.post(
            f"/api/v1/receipts/{rec['id']}/allocate",
            json={"invoice_id": inv["id"], "amount": "1210.00"},
        )
    ).json()
    entry_id = detail["allocations"][0]["id"]
    assert (
        await auth_client.post(
            f"/api/v1/receipts/{rec['id']}/deallocate", json={"payment_id": entry_id}
        )
    ).status_code == 200
    again = await auth_client.post(
        f"/api/v1/receipts/{rec['id']}/deallocate", json={"payment_id": entry_id}
    )
    assert again.status_code == 400 and "already reversed" in again.json()["detail"]


@pytest.mark.asyncio
async def test_deallocate_unknown_entry_refused(auth_client):
    await _activate(auth_client)
    rec = await _receipt(auth_client, "100.00")
    r = await auth_client.post(
        f"/api/v1/receipts/{rec['id']}/deallocate", json={"payment_id": "no-such-entry"}
    )
    assert r.status_code == 400 and "not found" in r.json()["detail"]


@pytest.mark.asyncio
async def test_ledger_healthy_after_reversal(auth_client):
    await _activate(auth_client)
    inv = await _invoice(auth_client)
    rec = await _receipt(auth_client, "1210.00")
    detail = (
        await auth_client.post(
            f"/api/v1/receipts/{rec['id']}/allocate",
            json={"invoice_id": inv["id"], "amount": "1210.00"},
        )
    ).json()
    await auth_client.post(
        f"/api/v1/receipts/{rec['id']}/deallocate",
        json={"payment_id": detail["allocations"][0]["id"]},
    )
    report = (await auth_client.post("/api/v1/integrity/ledger/verify")).json()
    assert report["healthy"] and report["issues"] == [], report


@pytest.mark.asyncio
async def test_payment_authz_read_write_split(auth_client, client):
    await _activate(auth_client)
    inv = await _invoice(auth_client)
    rec = await _receipt(auth_client, "1210.00")

    # EMPLOYEE (role 'user') has no PAYMENT_READ → the router gate 403s everything.
    emp = await _member(auth_client, client, "emp@corp.io", role="user")
    assert (await client.get("/api/v1/receipts", headers=_h(emp))).status_code == 403

    # READ_ONLY (role 'user_free') has PAYMENT_READ but not PAYMENT_WRITE.
    ro = await _member(auth_client, client, "ro@corp.io", role="user_free")
    assert (await client.get("/api/v1/receipts", headers=_h(ro))).status_code == 200
    assert (
        await client.post(
            "/api/v1/receipts",
            headers=_h(ro),
            json={"amount": "10.00", "received_on": "2026-02-05"},
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/receipts/{rec['id']}/allocate",
            headers=_h(ro),
            json={"invoice_id": inv["id"], "amount": "10.00"},
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_receivables_aging_csv(auth_client):
    await _activate(auth_client)
    await _invoice(auth_client)  # an open, past-due receivable
    r = await auth_client.get("/api/v1/issued/reports/receivables?format=csv&view=aging")
    assert r.status_code == 200
    text = r.text
    assert "bucket" in text.splitlines()[0]
    # The standard aging buckets are present.
    assert "Current" in text and "90+" in text
