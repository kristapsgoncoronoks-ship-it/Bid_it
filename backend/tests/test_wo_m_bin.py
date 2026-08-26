"""WO-M — the bin extends to all entities + the invoice→VAT-claim refusal.

What must hold:
- deleting an expense report / inbox transaction / recurring schedule /
  issued-invoice attachment STAMPS instead of destroys: the row vanishes
  from ordinary reads, appears in the generic Trash listing with days left,
  and restore brings it back;
- the purge destroys only rows past the 30-day window, audited with WHAT
  was destroyed;
- an AP invoice that a FROZEN line of a filed claim resolved to cannot be
  deleted — hard 409 `invoice_backs_filed_claim`, no consent ceremony —
  and the bulk path skips it with the same reason; a withdrawn claim
  releases it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.expense import ExpenseTransaction
from app.models.organization import Organization
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services import bin as bin_svc

pytestmark = pytest.mark.asyncio

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


async def _org_id(db_session) -> str:
    return await db_session.scalar(select(Organization.id))


async def _enable(auth_client, *mods):
    for m in mods:
        assert (
            await auth_client.put(f"/api/v1/modules/{m}", json={"enabled": True})
        ).status_code == 200


async def test_expense_report_bins_lists_and_restores(auth_client):
    await _enable(auth_client, "expenses")
    r = await auth_client.post(
        "/api/v1/expenses",
        json={
            "title": "Overlap trip",
            "currency": "EUR",
            "items": [
                {
                    "spend_date": "2026-05-01",
                    "category": "travel",
                    "description": "Train ticket",
                    "amount": "42.00",
                    "vat_amount": "0",
                }
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    rid = r.json()["id"]

    assert (await auth_client.delete(f"/api/v1/expenses/{rid}")).status_code == 204
    # Hidden from the ordinary read…
    assert (await auth_client.get(f"/api/v1/expenses/{rid}")).status_code == 404
    # …present in the generic Trash with days left…
    trash = (await auth_client.get("/api/v1/invoices/trash/other")).json()
    mine = [i for i in trash["items"] if i["id"] == rid]
    assert mine and mine[0]["kind"] == "expense_report"
    assert mine[0]["days_left"] > 0
    # …and restorable.
    back = await auth_client.post(f"/api/v1/invoices/trash/other/expense_report/{rid}/restore")
    assert back.status_code == 200, back.text
    assert (await auth_client.get(f"/api/v1/expenses/{rid}")).status_code == 200


async def test_recurring_schedule_and_attachment_bin(auth_client):
    await _enable(auth_client, "issuing")
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200

    rec = await auth_client.post(
        "/api/v1/issued/recurring",
        json={
            "template": {
                "buyer_name": "Globex SARL",
                "issue_date": "2026-08-01",
                "lines": [
                    {
                        "description": "Retainer",
                        "quantity": "1",
                        "unit_price": "100",
                        "vat_rate": "0",
                    }
                ],
            },
            "frequency": "monthly",
            "start_date": "2026-09-01",
        },
    )
    assert rec.status_code in (200, 201), rec.text
    rec_id = rec.json()["id"]
    assert (await auth_client.delete(f"/api/v1/issued/recurring/{rec_id}")).status_code == 204

    inv = await auth_client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "Globex SARL",
            "issue_date": "2026-08-01",
            "lines": [
                {"description": "Work", "quantity": "1", "unit_price": "100", "vat_rate": "0"}
            ],
        },
    )
    assert inv.status_code in (200, 201), inv.text
    inv_id = inv.json()["id"]
    up = await auth_client.post(
        f"/api/v1/issued/{inv_id}/attachments",
        files={"file": ("timesheet.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
    )
    assert up.status_code in (200, 201), up.text
    att_id = up.json()["id"]
    assert (
        await auth_client.delete(f"/api/v1/issued/{inv_id}/attachments/{att_id}")
    ).status_code == 204

    trash = (await auth_client.get("/api/v1/invoices/trash/other")).json()
    kinds = {i["id"]: i["kind"] for i in trash["items"]}
    assert kinds.get(rec_id) == "recurring_schedule"
    assert kinds.get(att_id) == "issued_attachment"

    # Restore both; they come back to their ordinary reads.
    assert (
        await auth_client.post(f"/api/v1/invoices/trash/other/recurring_schedule/{rec_id}/restore")
    ).status_code == 200
    assert (
        await auth_client.post(f"/api/v1/invoices/trash/other/issued_attachment/{att_id}/restore")
    ).status_code == 200
    recs = (await auth_client.get("/api/v1/issued/recurring")).json()
    assert any(x["id"] == rec_id for x in recs)


async def test_purge_destroys_only_past_the_window(auth_client, db_session):
    org_id = await _org_id(db_session)
    me = (await auth_client.get("/api/v1/auth/me")).json()["user"]["id"]
    old = ExpenseTransaction(
        org_id=org_id,
        employee_id=me,
        txn_date=datetime.now(UTC).date(),
        description="Old inbox line",
        amount=Decimal("10.00"),
        deleted_at=datetime.now(UTC) - timedelta(days=31),
        deleted_by="someone",
    )
    fresh = ExpenseTransaction(
        org_id=org_id,
        employee_id=me,
        txn_date=datetime.now(UTC).date(),
        description="Fresh inbox line",
        amount=Decimal("11.00"),
        deleted_at=datetime.now(UTC) - timedelta(days=1),
        deleted_by="someone",
    )
    db_session.add_all([old, fresh])
    await db_session.commit()

    out = await bin_svc.purge_expired(db_session, org_id)
    await db_session.commit()
    assert out["purged"] == 1
    assert out["records"][0]["id"] == old.id
    assert out["records"][0]["summary"]["description"] == "Old inbox line"

    # The fresh one survived, still in the bin.
    trash = (await auth_client.get("/api/v1/invoices/trash/other")).json()
    ids = {i["id"] for i in trash["items"]}
    assert fresh.id in ids and old.id not in ids


async def _ap_invoice(auth_client, number="INV-EVIDENCE-1") -> str:
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Overlap Supplies GmbH",
            "invoice_number": number,
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Diesel",
                    "quantity": "1",
                    "unit_price": "100",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _claim_backing(db_session, org_id: str, invoice_id: str, *, status="submitted"):
    from app.models.issuer import IssuerProfile

    entity = IssuerProfile(org_id=org_id, name="Own Entity OU", legal_name="Own Entity OU")
    db_session.add(entity)
    await db_session.flush()
    claim = VatRefundClaim(
        org_id=org_id,
        entity_id=entity.id,
        refund_country="LV",
        ref_period="2026-Q2",
        status=status,
    )
    db_session.add(claim)
    await db_session.flush()
    db_session.add(
        VatRefundClaimLine(
            org_id=org_id,
            claim_id=claim.id,
            invoice_ref="INV-EVIDENCE-1",
            invoice_id=invoice_id,
            product_group="DIESEL",
            net_eur=Decimal("100.00"),
            vat_eur=Decimal("21.00"),
            frozen_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    return claim


async def test_claim_evidence_refuses_deletion_until_withdrawn(auth_client, db_session):
    org_id = await _org_id(db_session)
    iid = await _ap_invoice(auth_client)
    claim = await _claim_backing(db_session, org_id, iid, status="submitted")

    gone = await auth_client.delete(f"/api/v1/invoices/{iid}")
    assert gone.status_code == 409, gone.text
    body = gone.json()
    assert "VAT refund claim" in str(body)

    # Approved and rejected claims keep the evidence too.
    for st in ("approved", "rejected"):
        claim.status = st
        await db_session.commit()
        assert (await auth_client.delete(f"/api/v1/invoices/{iid}")).status_code == 409

    # A WITHDRAWN claim released it — the ordinary delete flow resumes.
    claim.status = "withdrawn"
    await db_session.commit()
    gone = await auth_client.delete(f"/api/v1/invoices/{iid}")
    assert gone.status_code in (200, 204, 409), gone.text
    if gone.status_code == 409:
        # The consent ceremony, not the claim refusal — acknowledge and retry.
        assert "claim" not in str(gone.json()).lower()


async def test_bulk_delete_skips_claim_evidence(auth_client, db_session):
    org_id = await _org_id(db_session)
    iid = await _ap_invoice(auth_client, number="INV-EVIDENCE-2")
    await _claim_backing(db_session, org_id, iid, status="paid")

    r = await auth_client.post(
        "/api/v1/invoices/bulk-delete",
        json={"invoice_ids": [iid], "selection": "explicit", "agreed_count": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skipped"] == 1
    skip = next(o for o in body["outcomes"] if o["result"] == "skipped")
    assert "claim" in skip["reason"].lower()
    # Still alive.
    assert (await auth_client.get(f"/api/v1/invoices/{iid}")).status_code == 200
