"""The WO-2 fraud-safety invariant: protected vendor fields (`iban`, `tax_id`)
on an EXISTING vendor are never written directly — they go through a pending
change request a SECOND approver applies (maker ≠ checker), payment runs refuse
vendors with an in-flight change or an unverified (provisional) identity, and
the SEPA builder is the last line of defence against an invalid creditor IBAN.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.models.audit import AuditEvent
from app.models.vendor import Vendor
from app.models.vendor_change_request import VendorChangeRequest

IBAN_A = "DE89370400440532013000"
IBAN_B = "NL91ABNA0417164300"
IBAN_C = "BE68539007547034"

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": IBAN_B,
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _member(auth_client, client, email, role="admin"):
    """A second org member (default: admin ⇒ holds SETTINGS_MANAGE)."""
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _vendor_with_iban(auth_client, name, iban=IBAN_A):
    r = await auth_client.post("/api/v1/vendors", json={"name": name, "iban": iban})
    assert r.status_code == 201, r.text
    return r.json()


async def _scheduled_invoice(auth_client, approver_token, *, vendor, number):
    """An approved, scheduled-for-payment invoice for `vendor` (creates the
    vendor via capture if absent — such a vendor is ACTIVE with no IBAN)."""
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": vendor,
            "invoice_number": number,
            "issue_date": "2026-05-01",
            "due_date": "2026-12-01",
            "currency": "EUR",
            "line_items": [
                {"description": "W", "quantity": "1", "unit_price": "100", "tax_rate": "0"}
            ],
        },
    )
    iid = r.json()["id"]
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    appr = await auth_client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(approver_token),
        json={"version": sub.json()["version"]},
    )
    await auth_client.post(
        f"/api/v1/invoices/{iid}/transition",
        json={"version": appr.json()["version"], "target": "scheduled_for_payment"},
    )
    return iid


@pytest.mark.asyncio
async def test_iban_change_on_existing_vendor_does_not_mutate_the_row(auth_client, db_session):
    v = await _vendor_with_iban(auth_client, "Steel GmbH")
    r = await auth_client.patch(f"/api/v1/vendors/{v['id']}", json={"iban": IBAN_B})
    assert r.status_code == 200, r.text
    body = r.json()
    # The stored IBAN is UNCHANGED — the new value appears only as a pending block.
    assert body["iban"] == IBAN_A
    assert len(body["pending_changes"]) == 1
    assert body["pending_changes"][0]["field"] == "iban"
    assert body["pending_changes"][0]["status"] == "pending"
    row = await db_session.scalar(select(Vendor).where(Vendor.id == v["id"]))
    assert row.iban == IBAN_A, f"stored IBAN mutated to {row.iban!r}"
    reqs = list(
        await db_session.scalars(
            select(VendorChangeRequest).where(VendorChangeRequest.vendor_id == v["id"])
        )
    )
    assert len(reqs) == 1 and reqs[0].status == "pending" and reqs[0].new_value == IBAN_B


@pytest.mark.asyncio
async def test_tax_id_change_creates_pending_request(auth_client, db_session):
    r = await auth_client.post("/api/v1/vendors", json={"name": "Tax Co", "tax_id": "EE999999999"})
    assert r.status_code == 201
    vid = r.json()["id"]
    upd = await auth_client.patch(f"/api/v1/vendors/{vid}", json={"tax_id": "LV99999999999"})
    assert upd.status_code == 200
    assert upd.json()["tax_id"] == "EE999999999"  # unchanged
    req = await db_session.scalar(
        select(VendorChangeRequest).where(VendorChangeRequest.vendor_id == vid)
    )
    assert req is not None and req.field == "tax_id" and req.status == "pending"
    assert req.old_value == "EE999999999" and req.new_value == "LV99999999999"


@pytest.mark.asyncio
async def test_requester_cannot_approve_own_change(auth_client):
    """Segregation of duties (§4.8): the owner holds SETTINGS_MANAGE, yet may
    not approve a change they themselves requested."""
    v = await _vendor_with_iban(auth_client, "SoD GmbH")
    r = await auth_client.patch(f"/api/v1/vendors/{v['id']}", json={"iban": IBAN_B})
    req_id = r.json()["pending_changes"][0]["id"]
    denied = await auth_client.post(f"/api/v1/vendors/changes/{req_id}/approve", json={})
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "maker_is_checker"


@pytest.mark.asyncio
async def test_second_approver_applies_change_and_audits(auth_client, client, db_session):
    approver = await _member(auth_client, client, "checker@acme.io", role="admin")
    v = await _vendor_with_iban(auth_client, "Approved GmbH")
    r = await auth_client.patch(f"/api/v1/vendors/{v['id']}", json={"iban": IBAN_B})
    req_id = r.json()["pending_changes"][0]["id"]
    ok = await auth_client.post(
        f"/api/v1/vendors/changes/{req_id}/approve", headers=_h(approver), json={}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"
    row = await db_session.scalar(select(Vendor).where(Vendor.id == v["id"]))
    assert row.iban == IBAN_B  # value applied
    assert row.version == 2  # bumped by the approval
    event = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "vendor.change_approved", AuditEvent.target_id == v["id"]
        )
    )
    assert event is not None and event.meta is not None
    assert '"old"' in event.meta and '"new"' in event.meta  # old→new recorded


@pytest.mark.asyncio
async def test_rejected_change_leaves_value_and_audits(auth_client, client, db_session):
    approver = await _member(auth_client, client, "rejector@acme.io", role="admin")
    v = await _vendor_with_iban(auth_client, "Rejected GmbH")
    r = await auth_client.patch(f"/api/v1/vendors/{v['id']}", json={"iban": IBAN_B})
    req_id = r.json()["pending_changes"][0]["id"]
    rej = await auth_client.post(
        f"/api/v1/vendors/changes/{req_id}/reject",
        headers=_h(approver),
        json={"note": "does not match the supplier letterhead"},
    )
    assert rej.status_code == 200 and rej.json()["status"] == "rejected"
    row = await db_session.scalar(select(Vendor).where(Vendor.id == v["id"]))
    assert row.iban == IBAN_A  # untouched
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "vendor.change_rejected")
    )
    assert event is not None and "letterhead" in (event.meta or "")


@pytest.mark.asyncio
async def test_duplicate_open_request_for_same_field_is_refused(auth_client, db_session):
    v = await _vendor_with_iban(auth_client, "Dup GmbH")
    first = await auth_client.patch(f"/api/v1/vendors/{v['id']}", json={"iban": IBAN_B})
    assert len(first.json()["pending_changes"]) == 1
    second = await auth_client.patch(f"/api/v1/vendors/{v['id']}", json={"iban": IBAN_C})
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "duplicate_change_request"
    count = len(
        list(
            await db_session.scalars(
                select(VendorChangeRequest).where(VendorChangeRequest.vendor_id == v["id"])
            )
        )
    )
    assert count == 1  # still exactly one open request


@pytest.mark.asyncio
async def test_new_vendor_with_captured_iban_is_provisional(auth_client):
    v = await _vendor_with_iban(auth_client, "Fresh Capture BV")
    assert v["status"] == "provisional"
    # A bare vendor (no captured bank/tax identity) stays active.
    bare = await auth_client.post("/api/v1/vendors", json={"name": "Bare BV"})
    assert bare.json()["status"] == "active"


@pytest.mark.asyncio
async def test_payment_run_refuses_vendor_with_pending_iban_change(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    # Vendor born via invoice capture (active); first IBAN capture is direct...
    iid = await _scheduled_invoice(auth_client, approver, vendor="Pending AG", number="INV-P1")
    vendors = (await auth_client.get("/api/v1/vendors")).json()
    vid = next(x["id"] for x in vendors if x["name"] == "Pending AG")
    first = await auth_client.patch(f"/api/v1/vendors/{vid}", json={"iban": IBAN_A})
    assert first.json()["iban"] == IBAN_A and first.json()["pending_changes"] == []
    # ...but a CHANGE to it goes pending — and blocks the payout run BY NAME.
    await auth_client.patch(f"/api/v1/vendors/{vid}", json={"iban": IBAN_B})
    run = await auth_client.post("/api/v1/payment-runs", json={"invoice_ids": [iid]})
    assert run.status_code == 422, run.text
    assert "Pending AG" in run.json()["detail"]
    assert "pending" in run.json()["detail"].lower()


@pytest.mark.asyncio
async def test_payment_run_refuses_provisional_vendor_unless_confirmed(auth_client, client):
    approver = await _member(auth_client, client, "appr2@acme.io", role="admin")
    # Vendor created ALREADY carrying a captured IBAN ⇒ provisional.
    await _vendor_with_iban(auth_client, "Prov AG")
    iid = await _scheduled_invoice(auth_client, approver, vendor="Prov AG", number="INV-P2")
    refused = await auth_client.post("/api/v1/payment-runs", json={"invoice_ids": [iid]})
    assert refused.status_code == 422
    assert "Prov AG" in refused.json()["detail"]
    confirmed = await auth_client.post(
        "/api/v1/payment-runs", json={"invoice_ids": [iid], "confirm_provisional": True}
    )
    assert confirmed.status_code == 201, confirmed.text


@pytest.mark.asyncio
async def test_sepa_build_refuses_invalid_creditor_iban(auth_client, client, db_session):
    """Last line of defence: an IBAN invalidated BELOW the app (direct DB edit /
    bad migration) must abort the pain.001 build — no XML is ever returned."""
    approver = await _member(auth_client, client, "appr3@acme.io", role="admin")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    iid = await _scheduled_invoice(auth_client, approver, vendor="Corrupt AG", number="INV-P3")
    vendors = (await auth_client.get("/api/v1/vendors")).json()
    vid = next(x["id"] for x in vendors if x["name"] == "Corrupt AG")
    await auth_client.patch(f"/api/v1/vendors/{vid}", json={"iban": IBAN_A})  # first capture
    run = (await auth_client.post("/api/v1/payment-runs", json={"invoice_ids": [iid]})).json()
    await auth_client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        json={"version": run["version"], "reference": "SEPA-X"},
    )
    # Corrupt the stored IBAN beneath every app-level write gate.
    await db_session.execute(
        update(Vendor).where(Vendor.id == vid).values(iban="DE00000000000000000000")
    )
    await db_session.commit()
    r = await auth_client.get(f"/api/v1/payment-runs/{run['id']}/sepa")
    assert r.status_code == 422, r.text
    assert "Corrupt AG" in r.json()["detail"]
    assert "<?xml" not in r.text  # no partial file leaked


@pytest.mark.asyncio
async def test_audit_meta_never_contains_a_full_iban(auth_client, client, db_session):
    """Scan EVERY audit row written by the create→request→approve lifecycle:
    the full account numbers must never appear in the immutable audit meta."""
    approver = await _member(auth_client, client, "masked@acme.io", role="admin")
    v = await _vendor_with_iban(auth_client, "Masked GmbH", iban=IBAN_A)
    r = await auth_client.patch(f"/api/v1/vendors/{v['id']}", json={"iban": IBAN_B})
    req_id = r.json()["pending_changes"][0]["id"]
    await auth_client.post(
        f"/api/v1/vendors/changes/{req_id}/approve", headers=_h(approver), json={}
    )
    events = list(await db_session.scalars(select(AuditEvent)))
    assert any(e.action.startswith("vendor.") for e in events)
    for e in events:
        blob = (e.meta or "") + (e.action or "")
        assert IBAN_A not in blob, f"full IBAN leaked into audit meta of {e.action}"
        assert IBAN_B not in blob, f"full IBAN leaked into audit meta of {e.action}"
