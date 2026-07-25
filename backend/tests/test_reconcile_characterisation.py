"""Characterisation tests for the submit-time reconciliation gate (WO-7).

These pin the CURRENT wire behaviour of the blocking reconciliation at
`POST /api/v1/invoices/{id}/submit` — status codes and exact message text —
so the move of `_reconcile` from the route module into the validation service
is provably behaviour-preserving. They passed against the pre-refactor code
and must keep passing UNMODIFIED afterwards (§4.20: the refactor is invisible
over the wire).

Header/line tampering goes straight through the DB session: the public create
endpoint recomputes every total server-side (§4.10), so an unbalanced invoice
can only exist via capture drift or a partial write — exactly the state the
submit gate exists to refuse.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.models.invoice import Invoice, LineItem


async def _make_invoice(auth_client, number="REC-1", qty="2", unit="50", tax="20", amount=None):
    line = {"description": "Widgets", "quantity": qty, "unit_price": unit, "tax_rate": tax}
    if amount is not None:
        line["amount"] = amount
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Acme Supplies",
            "invoice_number": number,
            "issue_date": "2026-05-01",
            "currency": "EUR",
            "line_items": [line],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _submit(auth_client, iid, version=1):
    return await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": version})


async def _tamper_invoice(db_session, iid, **fields):
    inv = await db_session.scalar(select(Invoice).where(Invoice.id == iid))
    for k, v in fields.items():
        setattr(inv, k, v)
    await db_session.commit()


async def _tamper_line(db_session, iid, **fields):
    li = await db_session.scalar(select(LineItem).where(LineItem.invoice_id == iid))
    for k, v in fields.items():
        setattr(li, k, v)
    await db_session.commit()


@pytest.mark.asyncio
async def test_negative_tax_rate_blocks_submit(auth_client, db_session):
    iid = await _make_invoice(auth_client, "REC-NEG")
    await _tamper_line(db_session, iid, tax_rate=Decimal("-5"))
    r = await _submit(auth_client, iid)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Invoice does not reconcile: ")
    assert "Line 'Widgets': tax rate -5.00% is out of range 0–100" in detail


@pytest.mark.asyncio
async def test_tax_rate_over_100_blocks_submit(auth_client, db_session):
    iid = await _make_invoice(auth_client, "REC-BIG")
    await _tamper_line(db_session, iid, tax_rate=Decimal("150"))
    r = await _submit(auth_client, iid)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Invoice does not reconcile: ")
    assert "Line 'Widgets': tax rate 150.00% is out of range 0–100" in detail


@pytest.mark.asyncio
async def test_line_amount_vs_qty_times_price_blocks_submit(auth_client):
    # amount override is legal at create (header totals follow the amounts), but
    # amount ≠ q2(quantity × unit_price) refuses submit — zero tolerance.
    iid = await _make_invoice(auth_client, "REC-AMT", qty="3", unit="100", tax="0", amount="250")
    r = await _submit(auth_client, iid)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Invoice does not reconcile: "
        "Line 'Widgets': amount 250.00 ≠ quantity × unit price (300.00)"
    )


@pytest.mark.asyncio
async def test_header_subtotal_mismatch_blocks_submit(auth_client, db_session):
    iid = await _make_invoice(auth_client, "REC-SUB")
    await _tamper_invoice(db_session, iid, subtotal=Decimal("999.00"))
    r = await _submit(auth_client, iid)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Invoice does not reconcile: Subtotal mismatch: lines total 100.00 vs stated 999.00"
    )


@pytest.mark.asyncio
async def test_header_tax_mismatch_blocks_submit(auth_client, db_session):
    iid = await _make_invoice(auth_client, "REC-TAX")
    await _tamper_invoice(db_session, iid, tax_amount=Decimal("5.00"))
    r = await _submit(auth_client, iid)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Invoice does not reconcile: Tax mismatch: lines tax 20.00 vs stated 5.00"
    )


@pytest.mark.asyncio
async def test_header_total_mismatch_blocks_submit(auth_client, db_session):
    iid = await _make_invoice(auth_client, "REC-TOT")
    await _tamper_invoice(db_session, iid, total=Decimal("999.00"))
    r = await _submit(auth_client, iid)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Invoice does not reconcile: Total mismatch: computed 120.00 vs stated 999.00"
    )


@pytest.mark.asyncio
async def test_zero_line_items_blocks_submit(auth_client, db_session):
    iid = await _make_invoice(auth_client, "REC-EMPTY")
    await db_session.execute(delete(LineItem).where(LineItem.invoice_id == iid))
    await db_session.commit()
    r = await _submit(auth_client, iid)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "Add at least one line first."


@pytest.mark.asyncio
async def test_balanced_invoice_submits_with_exact_reconciliation(auth_client):
    iid = await _make_invoice(auth_client, "REC-OK")  # 2 × 50 @ 20% = 100 + 20
    r = await _submit(auth_client, iid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workflow_state"] == "submitted"
    assert body["reconciliation"] == {
        "computed_subtotal": "100.00",
        "computed_tax": "20.00",
        "computed_total": "120.00",
        "stated_subtotal": "100.00",
        "stated_tax": "20.00",
        "stated_total": "120.00",
        "balanced": True,
        "findings": [],
    }


@pytest.mark.asyncio
async def test_review_payload_reconciliation_reports_findings_without_blocking(
    auth_client, db_session
):
    """GET /review surfaces the same reconciliation read-only — unbalanced is
    reported (balanced=False + message list), never an error at read time."""
    iid = await _make_invoice(auth_client, "REC-VIEW")
    await _tamper_invoice(db_session, iid, total=Decimal("999.00"))
    r = await auth_client.get(f"/api/v1/invoices/{iid}/review")
    assert r.status_code == 200, r.text
    recon = r.json()["reconciliation"]
    assert recon["balanced"] is False
    assert recon["findings"] == ["Total mismatch: computed 120.00 vs stated 999.00"]
