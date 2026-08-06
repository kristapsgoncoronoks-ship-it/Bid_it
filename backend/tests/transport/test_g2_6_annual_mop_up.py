"""G2.6 slice 2 — R6, `BA_fleet_fuel.md` C6: an ANNUAL claim is the MOP-UP
(invoices already locked to a quarter are silently excluded, not a
conflict); a QUARTERLY claim treats ANY overlap with an existing lock as a
duplicate and blocks the whole submission; an annual claim left with an
empty set after exclusion is refused. See
`app.services.transport.lock._apply_annual_mop_up_or_duplicate_block`'s
docstring for the exact semantics, including the fail-closed interpretation
for an annual-vs-annual overlap (not described in the harvested text).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.vat_claim import VatRefundClaim
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, fuel_ingest, lock
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import (
    activate_entity,
    enable_transport,
    make_entity,
    make_org,
    register_documented_invoice,
)


async def _make_claim(db_session, org, entity, ref_period: str) -> VatRefundClaim:
    return await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period=ref_period
    )


async def _make_txn(db_session, org, entity, *, invoice_ref: str, period: str, line_seq: int = 1):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        period=period,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 1, 15),
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=Decimal("2000.00"),
        vat_local=Decimal("420.00"),
        gross_local=Decimal("2420.00"),
        net_eur=Decimal("2000.00"),
        vat_eur=Decimal("420.00"),
        invoice_ref=invoice_ref,
    )


async def _submit(db_session, org, claim, invoices, **kw):
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    result = await lock.submit_claim(
        db_session, org.id, claim_id=claim.id, invoices=invoices, today=date(2027, 1, 1), **kw
    )
    await db_session.commit()
    return result


@pytest.mark.asyncio
async def test_g2_6_annual_claim_excludes_a_quarter_locked_invoice_not_a_conflict(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    # WO-73 (R44): raised past the activation gate — the WO-60 fixture precedent.
    await activate_entity(db_session, org.id, entity.id, "LV")
    # WO-75 (R3): raised past the synthetic lock gate — registered,
    # documented invoices so the built lines RESOLVE (the WO-73 fixture
    # precedent; assertions unchanged).
    await register_documented_invoice(db_session, org.id, invoice_number="INV-Q1")
    await register_documented_invoice(db_session, org.id, invoice_number="INV-YEAR")

    q1 = await _make_claim(db_session, org, entity, "2026-Q1")
    txn_q1 = await _make_txn(db_session, org, entity, invoice_ref="INV-Q1", period="2026-01")
    await db_session.commit()
    await _submit(db_session, org, q1, [("Q8", "INV-Q1", txn_q1.id)])

    year = await _make_claim(db_session, org, entity, "2026-YEAR")
    txn_year = await _make_txn(
        db_session, org, entity, invoice_ref="INV-YEAR", period="2026-06", line_seq=2
    )
    await db_session.commit()

    # The annual claim's invoice list includes BOTH the already-quarter-
    # locked invoice AND a genuinely new one — the quarter-locked one is
    # excluded silently (not a conflict); the new one still locks.
    result = await _submit(
        db_session,
        org,
        year,
        [("Q8", "INV-Q1", txn_q1.id), ("Q8", "INV-YEAR", txn_year.id)],
    )
    assert result.status == "submitted"

    locks = (
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == year.id)
        )
    ).all()
    assert len(locks) == 1
    assert locks[0].invoice_ref == "INV-YEAR"


@pytest.mark.asyncio
async def test_g2_6_annual_claim_with_nothing_left_after_exclusion_is_refused(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    # WO-73 (R44): raised past the activation gate — the WO-60 fixture precedent.
    await activate_entity(db_session, org.id, entity.id, "LV")
    # WO-75 (R3): raised past the synthetic lock gate — registered,
    # documented invoices so the built lines RESOLVE (the WO-73 fixture
    # precedent; assertions unchanged).
    await register_documented_invoice(db_session, org.id, invoice_number="INV-Q1")

    q1 = await _make_claim(db_session, org, entity, "2026-Q1")
    txn = await _make_txn(db_session, org, entity, invoice_ref="INV-Q1", period="2026-01")
    await db_session.commit()
    await _submit(db_session, org, q1, [("Q8", "INV-Q1", txn.id)])

    year = await _make_claim(db_session, org, entity, "2026-YEAR")
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, year.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=year.id,
            invoices=[("Q8", "INV-Q1", txn.id)],  # the ONLY invoice — fully excluded
            today=date(2027, 1, 1),
        )
    assert exc.value.code == "empty_claim_set"
    assert exc.value.status == 422

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == year.id))
    assert fresh.status == "draft"


@pytest.mark.asyncio
async def test_g2_6_quarterly_claim_blocks_the_whole_submission_on_any_overlap(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    # WO-73 (R44): raised past the activation gate — the WO-60 fixture precedent.
    await activate_entity(db_session, org.id, entity.id, "LV")
    # WO-75 (R3): raised past the synthetic lock gate — registered,
    # documented invoices so the built lines RESOLVE (the WO-73 fixture
    # precedent; assertions unchanged).
    await register_documented_invoice(db_session, org.id, invoice_number="INV-SHARED")
    await register_documented_invoice(db_session, org.id, invoice_number="INV-NEW")

    q1 = await _make_claim(db_session, org, entity, "2026-Q1")
    txn_shared = await _make_txn(
        db_session, org, entity, invoice_ref="INV-SHARED", period="2026-01"
    )
    await db_session.commit()
    await _submit(db_session, org, q1, [("Q8", "INV-SHARED", txn_shared.id)])

    q2 = await _make_claim(db_session, org, entity, "2026-Q2")
    txn_new = await _make_txn(
        db_session, org, entity, invoice_ref="INV-NEW", period="2026-04", line_seq=2
    )
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, q2.id)
    await db_session.commit()

    # Q2 tries to claim ONE genuinely new invoice AND the one Q1 already
    # holds — the WHOLE submission blocks (not a partial success).
    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=q2.id,
            invoices=[("Q8", "INV-NEW", txn_new.id), ("Q8", "INV-SHARED", txn_shared.id)],
            today=date(2027, 1, 1),
        )
    assert exc.value.code == "duplicate_invoice_lock"
    assert exc.value.status == 409

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == q2.id))
    assert fresh.status == "draft"
    # Nothing locked for Q2 — not even the genuinely-new invoice.
    locks = (
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == q2.id)
        )
    ).all()
    assert locks == []


@pytest.mark.asyncio
async def test_g2_6_two_annual_claims_overlapping_the_same_invoice_is_a_blocking_duplicate(
    db_session,
):
    """Fail-closed edge case NOT described in the harvested text: an
    invoice already locked by ANOTHER annual claim (not a quarter) is
    treated as a real duplicate, never silently excluded."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    # WO-73 (R44): raised past the activation gate — the WO-60 fixture precedent.
    await activate_entity(db_session, org.id, entity.id, "LV")
    # WO-75 (R3): raised past the synthetic lock gate — registered,
    # documented invoices so the built lines RESOLVE (the WO-73 fixture
    # precedent; assertions unchanged).
    await register_documented_invoice(db_session, org.id, invoice_number="INV-A")
    await register_documented_invoice(db_session, org.id, invoice_number="INV-B")

    year_a = await _make_claim(db_session, org, entity, "2025-YEAR")
    txn = await _make_txn(db_session, org, entity, invoice_ref="INV-A", period="2025-06")
    await db_session.commit()
    await _submit(db_session, org, year_a, [("Q8", "INV-A", txn.id)])

    year_b = await _make_claim(db_session, org, entity, "2026-YEAR")
    # year_b needs its OWN qualifying transaction so it clears the Art. 17
    # minimum (R8) and the duplicate-block (R6) is what actually fires,
    # not the minimum gate — the invoice it TRIES to lock (INV-A) is a
    # separate, caller-supplied key, independent of what its own lines sum.
    await _make_txn(db_session, org, entity, invoice_ref="INV-B", period="2026-06", line_seq=2)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, year_b.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=year_b.id,
            invoices=[("Q8", "INV-A", txn.id)],
            today=date(2027, 1, 1),
        )
    assert exc.value.code == "duplicate_invoice_lock"


@pytest.mark.asyncio
async def test_g2_6_annual_mop_up_never_touches_a_different_entitys_locks(db_session):
    """Scoping sanity: the existing-locks lookup is scoped to THIS claim's
    own (entity, refund_country) — an identical invoice_ref locked under a
    DIFFERENT entity must not be treated as an overlap at all."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity_a = await make_entity(db_session, org.id, seed=1)
    entity_b = await make_entity(db_session, org.id, seed=2)
    # WO-73 (R44): raised past the activation gate — the WO-60 fixture precedent.
    await activate_entity(db_session, org.id, entity_a.id, "LV")
    await activate_entity(db_session, org.id, entity_b.id, "LV")
    # WO-75 (R3): raised past the synthetic lock gate — registered,
    # documented invoices so the built lines RESOLVE (the WO-73 fixture
    # precedent; assertions unchanged).
    await register_documented_invoice(db_session, org.id, invoice_number="INV-SAME")

    claim_a = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity_a.id, refund_country="LV", ref_period="2026-Q1"
    )
    txn_a = await _make_txn(db_session, org, entity_a, invoice_ref="INV-SAME", period="2026-01")
    await db_session.commit()
    await _submit(db_session, org, claim_a, [("Q8", "INV-SAME", txn_a.id)])

    claim_b_year = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity_b.id, refund_country="LV", ref_period="2026-YEAR"
    )
    txn_b = await _make_txn(db_session, org, entity_b, invoice_ref="INV-SAME", period="2026-03")
    await db_session.commit()

    result = await _submit(db_session, org, claim_b_year, [("Q8", "INV-SAME", txn_b.id)])
    assert result.status == "submitted"
