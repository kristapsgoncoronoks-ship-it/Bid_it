"""L-4 slice 3 — the first bulk operation that cannot be undone.

Everything before this applied the guards to a REVERSIBLE action, which meant
guard 4 (filter-selection refused for irreversible actions) was unit-tested and
never actually load-bearing. Deleting draft invoices is where it fires for real.

The bar is higher here for an obvious reason: every other guard failing produces
an annoyance, and this one failing produces a financial record that no longer
exists. So the tests are about what must NOT be destroyed at least as much as
what must.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.invoice import Invoice, WorkflowState


async def _draft(auth_client, number: str) -> str:
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Fictional Fuels OU",
            "invoice_number": number,
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Diesel",
                    "quantity": "1",
                    "unit_price": "10.00",
                    "amount": "10.00",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _delete(auth_client, ids: list[str], **kw):
    body: dict = {"invoice_ids": ids, "agreed_count": len(ids), "selection": "explicit"}
    body.update(kw)
    return await auth_client.post("/api/v1/invoices/bulk-delete", json=body)


@pytest.mark.asyncio
async def test_drafts_are_deleted_and_each_reports_its_own_outcome(auth_client, db_session):
    ids = [await _draft(auth_client, f"INV-BD-{i}") for i in range(3)]

    r = await _delete(auth_client, ids)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 3
    assert body["skipped"] == 0
    assert {o["ref_id"] for o in body["outcomes"]} == set(ids)

    remaining = list(await db_session.scalars(select(Invoice.id)))
    assert all(i not in remaining for i in ids)


@pytest.mark.asyncio
async def test_a_filter_selection_is_refused_outright(auth_client, db_session):
    """GUARD 4, finally load-bearing. "Everything matching this filter" is a set
    the operator never enumerated. For something unrecoverable the answer is no —
    not a narrowed scope, not a warning."""
    ids = [await _draft(auth_client, "INV-BD-F1")]

    r = await _delete(auth_client, ids, selection="filter")

    assert r.status_code == 422, r.text
    assert r.json()["code"] == "bulk_filter_not_allowed"
    # And nothing was destroyed on the way to refusing.
    assert len(list(await db_session.scalars(select(Invoice.id)))) == 1


@pytest.mark.asyncio
async def test_an_approved_invoice_is_skipped_not_deleted(auth_client, db_session):
    """The whole point of the state rule. A skip names the state so the operator
    can see WHY it survived."""
    keep = await _draft(auth_client, "INV-BD-APPROVED")
    drop = await _draft(auth_client, "INV-BD-DRAFT")
    inv = await db_session.scalar(select(Invoice).where(Invoice.id == keep))
    inv.workflow_state = WorkflowState.approved
    await db_session.commit()

    r = await _delete(auth_client, [keep, drop])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 1
    assert body["skipped"] == 1
    skip = next(o for o in body["outcomes"] if o["result"] == "skipped")
    assert skip["ref_id"] == keep
    assert "approved" in skip["reason"]

    survivors = set(await db_session.scalars(select(Invoice.id)))
    assert keep in survivors, "an approved invoice was destroyed"
    assert drop not in survivors


@pytest.mark.asyncio
async def test_an_invoice_with_a_payment_is_never_deleted(auth_client, db_session):
    """A draft carrying a payment is a ledger inconsistency waiting to happen —
    the payment would outlive the thing it paid."""
    paid = await _draft(auth_client, "INV-BD-PAID")
    inv = await db_session.scalar(select(Invoice).where(Invoice.id == paid))
    inv.amount_paid = 10
    await db_session.commit()

    r = await _delete(auth_client, [paid])

    body = r.json()
    assert body["deleted"] == 0
    assert body["skipped"] == 1
    assert "payment" in body["outcomes"][0]["reason"].lower()
    assert paid in set(await db_session.scalars(select(Invoice.id)))


@pytest.mark.asyncio
async def test_a_count_mismatch_destroys_nothing(auth_client, db_session):
    """GUARD 1 on the destructive path. The list moving under an operator is
    survivable when the action is an acknowledgement; here it is not."""
    ids = [await _draft(auth_client, f"INV-BD-C{i}") for i in range(2)]

    r = await _delete(auth_client, ids, agreed_count=5)

    assert r.status_code == 409, r.text
    assert r.json()["code"] == "bulk_count_mismatch"
    assert len(list(await db_session.scalars(select(Invoice.id)))) == 2


@pytest.mark.asyncio
async def test_the_audit_records_what_was_deleted_not_just_that_it_was(auth_client, db_session):
    """GUARD 3 on the destructive path, where it actually matters. Once the row is
    gone an id identifies nothing — an id-only trail cannot answer "what did we
    delete?" six months later."""
    ids = [await _draft(auth_client, "INV-BD-AUDIT")]

    r = await _delete(auth_client, ids)
    assert r.status_code == 200, r.text

    events = list(
        await db_session.scalars(select(AuditEvent).where(AuditEvent.action == "invoice.delete"))
    )
    assert len(events) == 1
    meta = json.loads(events[0].meta)  # audit meta is JSON TEXT
    assert meta["bulk"] is True
    rec = meta["records"][0]
    assert rec["invoice_number"] == "INV-BD-AUDIT"
    assert rec["total"] is not None
    assert rec["currency"] == "EUR"
    # The response hands the same snapshot back, so a client can show what went.
    assert r.json()["deleted_records"][0]["invoice_number"] == "INV-BD-AUDIT"


@pytest.mark.asyncio
async def test_a_batch_that_deletes_nothing_writes_no_audit_event(auth_client, db_session):
    keep = await _draft(auth_client, "INV-BD-NONE")
    inv = await db_session.scalar(select(Invoice).where(Invoice.id == keep))
    inv.workflow_state = WorkflowState.approved
    await db_session.commit()

    r = await _delete(auth_client, [keep])
    assert r.json()["deleted"] == 0

    events = list(
        await db_session.scalars(select(AuditEvent).where(AuditEvent.action == "invoice.delete"))
    )
    assert events == []


@pytest.mark.asyncio
async def test_another_tenants_invoice_is_a_skip_and_survives(auth_client, role_client, db_session):
    """Cross-tenant ids must be indistinguishable from nonexistent ones, and must
    certainly not be deleted."""
    mine = await _draft(auth_client, "INV-BD-MINE")
    other = await role_client("owner")
    theirs = await _draft(other, "INV-BD-THEIRS")

    r = await _delete(auth_client, [mine, theirs])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 1
    skip = next(o for o in body["outcomes"] if o["result"] == "skipped")
    assert skip["ref_id"] == theirs
    assert theirs in set(await db_session.scalars(select(Invoice.id))), (
        "a bulk delete reached across tenants"
    )


@pytest.mark.asyncio
async def test_bulk_delete_needs_the_delete_permission(role_client):
    employee = await role_client("user")  # EMPLOYEE — no INVOICE_DELETE

    r = await employee.post(
        "/api/v1/invoices/bulk-delete",
        json={"invoice_ids": ["x"], "agreed_count": 1, "selection": "explicit"},
    )

    assert r.status_code == 403, r.text
