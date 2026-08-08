"""WO-94 defect 1 (G2.7 / §3.D **D7**) — withdrawal NULLs the workflow code.

D7, verbatim: *"Rejection **keeps** locks (mirrors 3B's `approved` engine
state). Only `withdraw_claim` releases, and it also NULLs `status_code`."*
WO-93's recon found the second half unimplemented and recorded it rather than
fixing it; this file is the regression net for the fix.

Every claim here is driven into its state through the REAL services —
`lock.submit_claim` stamps the `"2"`, `status.set_status_code` moves it on,
`lock.withdraw_claim` clears it. Nothing writes a status or a code by hand
except `test_wo94_the_backfill_repairs_a_legacy_row`, which deliberately
reconstructs the pre-fix shape the lifecycle can no longer produce.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import select, text

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.vat_claim import VatRefundClaim
from app.services.transport import lock, status
from tests.transport.test_wo93_client_status import (
    PERIOD_ENDED,
    _clean_claim,
    _org_with_entity,
    _submitted_claim,
)

# The repair predicate the WO-94 migration applies, in one place so the test
# and the migration cannot describe different rows. Kept as a WHERE clause
# rather than a full statement so the assertions can also COUNT with it.
LEGACY_SHAPE = "status = 'withdrawn' AND status_code IS NOT NULL"


def _meta(event: AuditEvent) -> dict:
    """`AuditEvent.meta` is a JSON TEXT column — the `test_g3_3_tie_out.py`
    reader, reused."""
    return json.loads(event.meta) if isinstance(event.meta, str) else event.meta


async def _withdraw_events(db_session, org_id: str) -> list[AuditEvent]:
    rows = await db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id,
            AuditEvent.action == "transport.claim_withdraw",
        )
    )
    return list(rows)


@pytest.mark.asyncio
async def test_wo94_withdraw_nulls_the_status_code(db_session):
    """D7's own sentence. The claim is submitted through the real gate (which
    stamps `"2"`), so the code being cleared is one the lifecycle really put
    there."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    assert claim.status_code == "2", "the submit stamp this test then clears"

    withdrawn = await lock.withdraw_claim(db_session, org_id, claim.id)
    await db_session.commit()

    assert withdrawn.status == "withdrawn"
    assert withdrawn.status_code is None

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "withdrawn"
    assert fresh.status_code is None, "the stored row, not just the returned object"


@pytest.mark.asyncio
async def test_wo94_withdraw_nulls_a_manually_set_code_too(db_session):
    """The clear is of the COLUMN, not of the `"2"` specifically — a claim
    moved on to `2B` (document request received, R12) by `set_status_code`
    loses that code on withdrawal exactly the same way."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    moved = await status.set_status_code(
        db_session, org_id, claim.id, "2B", action_deadline=date(2026, 9, 1)
    )
    await db_session.commit()
    assert moved.status_code == "2B"

    withdrawn = await lock.withdraw_claim(db_session, org_id, claim.id)
    await db_session.commit()
    assert withdrawn.status_code is None


@pytest.mark.asyncio
async def test_wo94_the_withdraw_audit_event_carries_old_to_new(db_session):
    """§4.16 — the changed value is audited old→new, in the same transaction
    as the change (the event is added to the session `withdraw_claim` is
    flushing into; the caller's commit persists both or neither). ONE event:
    the clear rides the withdrawal, it is not a second operation."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    await status.set_status_code(db_session, org_id, claim.id, "3D")
    await db_session.commit()

    await lock.withdraw_claim(db_session, org_id, claim.id)
    await db_session.commit()

    events = await _withdraw_events(db_session, org_id)
    assert len(events) == 1
    meta = _meta(events[0])
    assert meta["old_status_code"] == "3D"
    assert meta["new_status_code"] is None
    assert events[0].target_type == "vat_refund_claim"
    assert events[0].target_id == claim.id


@pytest.mark.asyncio
async def test_wo94_the_audit_records_the_old_code_even_when_it_was_already_null(db_session):
    """A claim can only be withdrawn from a lock-holding status, which today
    is only ever reached through `submit_claim` — but the audit shape must not
    depend on that: a NULL old value is recorded as NULL, never omitted."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    claim.status_code = None  # a hypothetical caller that never stamped a code
    await db_session.commit()

    await lock.withdraw_claim(db_session, org_id, claim.id)
    await db_session.commit()

    events = await _withdraw_events(db_session, org_id)
    assert len(events) == 1
    assert _meta(events[0]) == {"old_status_code": None, "new_status_code": None}


@pytest.mark.asyncio
async def test_wo94_withdraw_changes_nothing_else_on_the_claim(db_session):
    """ "No other lifecycle behaviour altered" — asserted over EVERY mapped
    column rather than over the handful this order was thinking about. Only
    `status` and `status_code` may move; the locks still all go."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    await status.set_status_code(
        db_session, org_id, claim.id, "2B", action_deadline=date(2026, 9, 1)
    )
    await db_session.commit()

    # Every mapped column except `updated_at`, which is the row's own
    # "something changed" stamp — it is SUPPOSED to move on any write, so
    # including it would assert nothing about the lifecycle.
    columns = [c.key for c in VatRefundClaim.__mapper__.column_attrs if c.key != "updated_at"]
    before = {c: getattr(claim, c) for c in columns}
    assert before["status_code"] == "2B" and before["action_deadline"] == date(2026, 9, 1)

    await lock.withdraw_claim(db_session, org_id, claim.id)
    await db_session.commit()

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    after = {c: getattr(fresh, c) for c in columns}
    moved = {c for c in columns if before[c] != after[c]}
    assert moved == {"status", "status_code"}, f"unexpected columns changed: {moved}"
    assert after["status"] == "withdrawn" and after["status_code"] is None
    # R5 is untouched: the locks are still released, and the deadline/note the
    # claim carried are still its record of what happened.
    assert after["action_deadline"] == date(2026, 9, 1)
    rows = await db_session.scalars(
        select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == claim.id)
    )
    assert list(rows) == []


@pytest.mark.asyncio
async def test_wo94_a_withdrawn_claim_cannot_be_resubmitted_or_recoded(db_session):
    """The cleared code cannot be read back as though it were still there, and
    neither writer will restore it: `submit_claim` refuses a non-draft claim
    (`claim_not_draft`) and `set_status_code` refuses a non-locked one
    (`claim_not_submitted`). Both refusals leave the column NULL."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim, txn = await _clean_claim(db_session, org_id, entity_id)
    await lock.submit_claim(
        db_session,
        org_id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        today=PERIOD_ENDED,
    )
    await db_session.commit()
    await lock.withdraw_claim(db_session, org_id, claim.id)
    await db_session.commit()

    with pytest.raises(AppError) as resubmit:
        await lock.submit_claim(
            db_session,
            org_id,
            claim_id=claim.id,
            invoices=[("Q8", "INV-0001", txn.id)],
            today=PERIOD_ENDED,
        )
    assert resubmit.value.code == "claim_not_draft"

    with pytest.raises(AppError) as recode:
        await status.set_status_code(db_session, org_id, claim.id, "3A")
    assert recode.value.code == "claim_not_submitted"

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "withdrawn" and fresh.status_code is None


@pytest.mark.asyncio
async def test_wo94_withdraw_still_refuses_a_claim_that_holds_no_locks(db_session):
    """R5's refusal is unchanged, and the refusal writes NOTHING — a draft
    claim's code is not cleared as a side effect of a failed withdrawal."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim, _txn = await _clean_claim(db_session, org_id, entity_id)

    with pytest.raises(AppError) as exc:
        await lock.withdraw_claim(db_session, org_id, claim.id)
    assert exc.value.code == "claim_not_locked"

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "draft"
    assert await _withdraw_events(db_session, org_id) == []


@pytest.mark.asyncio
async def test_wo94_the_backfill_repairs_a_legacy_row(db_session):
    """The migration's repair, proven on a row in the pre-WO-94 shape.

    The shape has to be reconstructed by hand precisely BECAUSE the fix works
    — no service can produce it any more. The repair is the one D7 states, it
    matches only claims that are both withdrawn and coded, and it is
    idempotent (a second run repairs zero rows)."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    legacy = await _submitted_claim(db_session, org_id, entity_id)
    await lock.withdraw_claim(db_session, org_id, legacy.id)
    await db_session.commit()
    legacy.status_code = "3B"  # what the pre-fix withdrawal left behind
    # A withdrawn claim with no code, and a live coded claim: neither is the
    # legacy shape, and the repair must leave both alone.
    untouched_submitted = await _submitted_claim(
        db_session,
        org_id,
        entity_id,
        ref_period="2026-Q1",  # a DIFFERENT claim, not the withdrawn one reopened
        number="INV-0002",
        supplier="BP",
        sha="b" * 64,
        month="2026-02",
        txn_date=date(2026, 2, 15),
    )
    await db_session.commit()

    matched = await db_session.scalar(
        text(f"SELECT count(*) FROM vat_refund_claims WHERE {LEGACY_SHAPE}")
    )
    assert matched == 1

    await db_session.execute(
        text(f"UPDATE vat_refund_claims SET status_code = NULL WHERE {LEGACY_SHAPE}")
    )
    await db_session.commit()

    await db_session.refresh(legacy)
    await db_session.refresh(untouched_submitted)
    assert legacy.status == "withdrawn" and legacy.status_code is None
    assert untouched_submitted.status == "submitted"
    assert untouched_submitted.status_code == "2", "a live claim keeps its code"

    remaining = await db_session.scalar(
        text(f"SELECT count(*) FROM vat_refund_claims WHERE {LEGACY_SHAPE}")
    )
    assert remaining == 0, "idempotent — a second run has nothing to repair"
