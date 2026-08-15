"""Deleting invoices — the bulk path's guards, and the single path's consent gate.

Originally L-4 slice 3: "the first bulk operation that cannot be undone". The
recycle bin has since made deletion REVERSIBLE, so that framing no longer holds —
but guard 4 (filter-selection refused) was deliberately kept, because reversible
is not the same as harmless: hundreds of invoices vanishing from the books is a
real incident for however long it takes to work out which ones they were.

The two paths now apply DIFFERENT policies to the same facts. Bulk stays
drafts-only. Single-delete lets a client remove a consequential invoice — the
owner's explicit decision — warned every time, with the acceptance recorded.
"Every time" is exactly what one checkbox over two hundred invoices cannot
deliver, which is why the split exists.

"Deleted" throughout these tests means "invisible to every read": a binned row is
excluded from `select(Invoice.id)` by the central guard in `app.core.tenant`, so
these assertions read the same as they did when the row was destroyed.
tests/test_recycle_bin_restore.py is where the row's SURVIVAL is asserted.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.invoice import Invoice, WorkflowState
from app.services import deletion_consent


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


# --------------------------------------------------------------------------- #
# The SINGLE-invoice delete route, which until now removed an invoice in any
# state including paid. It enforces the same rule as the bulk path — and these
# tests prove it is the SAME rule, not a second copy that happens to agree today.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_deleting_one_draft_still_works(auth_client):
    """The narrowing must not break the legitimate case."""
    iid = await _draft(auth_client, "INV-SD-DRAFT")

    r = await auth_client.delete(f"/api/v1/invoices/{iid}")

    assert r.status_code == 204, r.text
    assert (await auth_client.get(f"/api/v1/invoices/{iid}")).status_code == 404


@pytest.mark.asyncio
async def test_deleting_one_approved_invoice_needs_the_warning_accepted_first(
    auth_client, db_session
):
    """The owner's decision, enforced on the SERVER: a client may delete an
    invoice past draft — it is their record — but only having been warned, every
    time, with the acceptance recorded.

    Superseded an outright refusal. That was the engineer's preference; the owner
    chose warn-and-confirm, knowingly."""
    iid = await _draft(auth_client, "INV-SD-APPROVED")
    inv = await db_session.scalar(select(Invoice).where(Invoice.id == iid))
    inv.workflow_state = WorkflowState.approved
    await db_session.commit()

    blocked = await auth_client.delete(f"/api/v1/invoices/{iid}")

    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body["code"] == "deletion_consent_required"
    # The 409 CARRIES the warning, so the words shown to the client are always
    # the server's current ones and cannot drift from what the audit trail says
    # was accepted.
    assert body["warning"]["version"] == deletion_consent.WARNING_VERSION
    assert body["warning"]["text"] == deletion_consent.WARNING_TEXT
    assert [c["code"] for c in body["warning"]["consequences"]] == ["not_draft"]
    assert iid in set(await db_session.scalars(select(Invoice.id))), "it deleted anyway"

    accepted = await auth_client.request(
        "DELETE",
        f"/api/v1/invoices/{iid}",
        json={"acknowledged_warning_version": deletion_consent.WARNING_VERSION},
    )

    assert accepted.status_code == 204, accepted.text
    assert iid not in set(await db_session.scalars(select(Invoice.id)))


@pytest.mark.asyncio
async def test_an_acknowledgement_of_an_OLDER_warning_is_refused(auth_client, db_session):
    """Consent given to different words is not consent to these. Without this the
    warning text could be rewritten and every stored acknowledgement would
    silently start claiming agreement to wording nobody ever saw."""
    iid = await _draft(auth_client, "INV-SD-STALE")
    inv = await db_session.scalar(select(Invoice).where(Invoice.id == iid))
    inv.workflow_state = WorkflowState.approved
    await db_session.commit()

    r = await auth_client.request(
        "DELETE",
        f"/api/v1/invoices/{iid}",
        json={"acknowledged_warning_version": "2020-01-01"},
    )

    assert r.status_code == 409, r.text
    assert r.json()["code"] == "deletion_consent_required"
    assert iid in set(await db_session.scalars(select(Invoice.id)))


@pytest.mark.asyncio
async def test_deleting_a_PAID_invoice_needs_the_warning_too_and_names_the_payment(
    auth_client, db_session
):
    iid = await _draft(auth_client, "INV-SD-PAID")
    inv = await db_session.scalar(select(Invoice).where(Invoice.id == iid))
    inv.amount_paid = 10
    await db_session.commit()

    r = await auth_client.delete(f"/api/v1/invoices/{iid}")

    assert r.status_code == 409, r.text
    assert [c["code"] for c in r.json()["warning"]["consequences"]] == ["payment_recorded"]
    assert iid in set(await db_session.scalars(select(Invoice.id)))


@pytest.mark.asyncio
async def test_a_clean_draft_deletes_with_no_ceremony(auth_client, db_session):
    """The gate must not fire on a record with no consequences. A warning shown
    for everything is a warning read for nothing — and this one has to be read."""
    iid = await _draft(auth_client, "INV-SD-CLEAN")

    r = await auth_client.delete(f"/api/v1/invoices/{iid}")

    assert r.status_code == 204, r.text


@pytest.mark.asyncio
async def test_the_accepted_warning_goes_into_the_audit_trail_verbatim(auth_client, db_session):
    """The owner's requirement: log that the client confirmed they read the
    warning. Storing a version alone would not survive the text being edited, so
    the words themselves are frozen into the event."""
    iid = await _draft(auth_client, "INV-SD-CONSENT-AUDIT")
    inv = await db_session.scalar(select(Invoice).where(Invoice.id == iid))
    inv.workflow_state = WorkflowState.approved
    await db_session.commit()

    await auth_client.request(
        "DELETE",
        f"/api/v1/invoices/{iid}",
        json={"acknowledged_warning_version": deletion_consent.WARNING_VERSION},
    )

    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "invoice.delete", AuditEvent.target_id == iid)
    )
    consent = json.loads(event.meta)["consent"]
    assert consent["warning_version"] == deletion_consent.WARNING_VERSION
    assert consent["warning_text"] == deletion_consent.WARNING_TEXT
    assert consent["consequences"] == ["not_draft"]


@pytest.mark.asyncio
async def test_a_plain_draft_deletion_records_NO_consent(auth_client, db_session):
    """An acceptance recorded for a decision nobody was asked about would make the
    whole trail untrustworthy — every consent entry has to mean a person read
    something."""
    iid = await _draft(auth_client, "INV-SD-NO-CONSENT")

    await auth_client.delete(f"/api/v1/invoices/{iid}")

    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "invoice.delete", AuditEvent.target_id == iid)
    )
    assert "consent" not in json.loads(event.meta)


@pytest.mark.asyncio
async def test_both_delete_paths_read_the_SAME_FACTS_though_their_policies_differ(
    auth_client, db_session
):
    """The anti-drift test, restated for a policy that deliberately forked.

    The two paths no longer agree on what to DO — single-delete warns and
    proceeds, bulk refuses outright, because "warn every time" is precisely what
    one checkbox over two hundred invoices cannot deliver. What they must never
    disagree about is the FACTS: both read `deletion_consent.consequences_for`,
    so an invoice cannot be consequential to one path and unremarkable to the
    other. Driving both and comparing the sentence is what makes that observable
    from outside."""
    single = await _draft(auth_client, "INV-SAME-1")
    batch = await _draft(auth_client, "INV-SAME-2")
    for iid in (single, batch):
        inv = await db_session.scalar(select(Invoice).where(Invoice.id == iid))
        inv.workflow_state = WorkflowState.approved
    await db_session.commit()

    one = await auth_client.delete(f"/api/v1/invoices/{single}")
    many = await _delete(auth_client, [batch])

    assert one.status_code == 409, one.text
    stated_to_the_single_path = one.json()["warning"]["consequences"][0]["message"]
    stated_by_the_bulk_path = many.json()["outcomes"][0]["reason"]
    assert stated_to_the_single_path in stated_by_the_bulk_path, (
        "the paths described the same invoice differently — the FACTS have forked, "
        f"single said {stated_to_the_single_path!r}, bulk said {stated_by_the_bulk_path!r}"
    )


@pytest.mark.asyncio
async def test_the_single_delete_audit_records_what_was_destroyed(auth_client, db_session):
    """It used to record only the number. After the row is gone, a number cannot
    say what the invoice was worth or who it was from."""
    iid = await _draft(auth_client, "INV-SD-AUDIT")

    assert (await auth_client.delete(f"/api/v1/invoices/{iid}")).status_code == 204

    event = await db_session.scalar(select(AuditEvent).where(AuditEvent.action == "invoice.delete"))
    meta = json.loads(event.meta)
    assert meta["bulk"] is False
    rec = meta["records"][0]
    assert rec["invoice_number"] == "INV-SD-AUDIT"
    assert rec["total"] is not None
    assert rec["currency"] == "EUR"
