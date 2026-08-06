"""G2.11 (WO-73) — the R44 activation gate inside `lock.submit_claim`
(`BA_fleet_fuel.md` R44: "submitting a claim for a prospect is refused
with the activation message"; §3.E's two activation bullets; D5's gate
order). Also proves the UNGATED surfaces: claim creation, line building,
the checklist and the close all still work for a non-active entity —
preparation-under-onboarding is what the harvested 1A stage describes,
only the legal act (submission) is gated."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, close, fuel_ingest, lock
from app.services.transport import customer_lifecycle as lc
from app.services.transport.checklist import submission_checklist
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import (
    activate_entity,
    enable_transport,
    make_entity,
    make_org,
    register_documented_invoice,
)


async def _make_claim(db_session, org, entity, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_txn(
    db_session,
    org,
    entity,
    *,
    period="2026-05",
    txn_date=date(2026, 5, 15),
    invoice_ref="INV-0001",
    net_eur=Decimal("2000.00"),
    vat_eur=Decimal("420.00"),
):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        period=period,
        line_seq=1,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=txn_date,
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        invoice_ref=invoice_ref,
    )


async def _submit(db_session, org, claim, txn, **kw):
    return await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        today=date(2026, 7, 1),
        **kw,
    )


async def _assert_nothing_mutated(db_session, org, claim):
    """The D5 'nothing mutated before the last gate passes' property: the
    claim is still draft, unfrozen, holds no locks."""
    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "draft"
    assert fresh.vat_eur is None
    locks = (
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == claim.id)
        )
    ).all()
    assert locks == []
    frozen = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(
                VatRefundClaimLine.claim_id == claim.id,
                VatRefundClaimLine.frozen_at.is_not(None),
            )
        )
    ).all()
    assert frozen == []


@pytest.mark.asyncio
async def test_g2_11_r44_acceptance_a_prospect_claim_is_refused_with_the_activation_message(
    db_session,
):
    """R44's acceptance line, verbatim."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await lc.add_prospect(db_session, org.id, entity.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await _submit(db_session, org, claim, txn)
    assert exc.value.code == "customer_not_active"
    assert exc.value.status == 409
    assert "'prospect'" in exc.value.message
    assert "activation" in exc.value.message  # the activation message

    await _assert_nothing_mutated(db_session, org, claim)


@pytest.mark.asyncio
async def test_g2_11_pending_inactive_and_unonboarded_are_refused_exactly_like_a_prospect(
    db_session,
):
    """F1: 'a prospect is ignored exactly like a pending customer' — and an
    entity with NO lifecycle row at all is refused too (fail CLOSED: the
    unonboarded must never out-privilege the onboarded pending)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)

    # (a) NO lifecycle row.
    entity = await make_entity(db_session, org.id, seed=1)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await _submit(db_session, org, claim, txn)
    assert exc.value.code == "customer_not_active"
    assert "'not onboarded'" in exc.value.message

    # (b) pending.
    await lc.add_prospect(db_session, org.id, entity.id)
    await lc.promote_prospect(db_session, org.id, entity.id)
    await db_session.commit()
    with pytest.raises(AppError) as exc:
        await _submit(db_session, org, claim, txn)
    assert exc.value.code == "customer_not_active"
    assert "'pending'" in exc.value.message

    # (c) inactive (a churned client — the commercial off-switch).
    await lc.set_activation(db_session, org.id, entity.id, True)
    await lc.set_inactive(db_session, org.id, entity.id)
    await db_session.commit()
    with pytest.raises(AppError) as exc:
        await _submit(db_session, org, claim, txn)
    assert exc.value.code == "customer_not_active"
    assert "'inactive'" in exc.value.message

    await _assert_nothing_mutated(db_session, org, claim)


@pytest.mark.asyncio
async def test_g2_11_an_active_customer_still_needs_the_refund_country_activated(db_session):
    """§3.E's second bullet: the refund country must be SEPARATELY
    activated — absence refuses, and `requested` (documents being
    gathered) refuses too; only `active` passes."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await lc.add_prospect(db_session, org.id, entity.id)
    await lc.promote_prospect(db_session, org.id, entity.id)
    await lc.set_activation(db_session, org.id, entity.id, True)
    # WO-75 (R3): raised past the synthetic lock gate — a registered,
    # documented invoice so the built line RESOLVES (the WO-73 fixture
    # precedent; assertions unchanged).
    await register_documented_invoice(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    # (a) country never requested.
    with pytest.raises(AppError) as exc:
        await _submit(db_session, org, claim, txn)
    assert exc.value.code == "country_not_activated"
    assert exc.value.status == 409
    assert "power of attorney" in exc.value.message

    # (b) requested but not yet activated.
    await lc.request_country(db_session, org.id, entity.id, "LV")
    await db_session.commit()
    with pytest.raises(AppError) as exc:
        await _submit(db_session, org, claim, txn)
    assert exc.value.code == "country_not_activated"
    await _assert_nothing_mutated(db_session, org, claim)

    # (c) the explicit admin click — the claim now submits through the
    # full existing gate stack.
    await lc.set_country_activation(db_session, org.id, entity.id, "LV", True)
    await db_session.commit()
    result = await _submit(db_session, org, claim, txn)
    assert result.status == "submitted"


@pytest.mark.asyncio
async def test_g2_11_gate_order_r7_and_r8_fire_before_r44(db_session):
    """D5: period-end and the Art. 17 minimum precede the engine-gate
    group the R44 gate heads — a prospect's mid-period or below-minimum
    claim reports the EARLIER gate's code."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await lc.add_prospect(db_session, org.id, entity.id)  # NOT active
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    # R7 before R44.
    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim.id,
            invoices=[("Q8", "INV-0001", txn.id)],
            today=date(2026, 6, 15),  # mid-period
        )
    assert exc.value.code == "period_not_ended"

    # R8 before R44 (a fresh, tiny claim in another period).
    claim2 = await _make_claim(db_session, org, entity, ref_period="2026-Q1")
    await db_session.commit()
    txn2 = await _make_txn(
        db_session,
        org,
        entity,
        period="2026-02",
        txn_date=date(2026, 2, 10),
        invoice_ref="INV-0002",
        net_eur=Decimal("10.00"),
        vat_eur=Decimal("2.10"),
    )
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim2.id)
    await db_session.commit()
    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim2.id,
            invoices=[("Q8", "INV-0002", txn2.id)],
            today=date(2026, 7, 1),
        )
    assert exc.value.code == "below_minimum"


@pytest.mark.asyncio
async def test_g2_11_gate_order_r44_fires_before_the_r6_duplicate_block(db_session):
    """§3.E places the activation gates at the HEAD of the engine-gate
    group — a churned (inactive) client's overlapping claim reports
    `customer_not_active`, never `duplicate_invoice_lock`."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await activate_entity(db_session, org.id, entity.id, "LV")
    # WO-75 (R3): raised past the synthetic lock gate — a registered,
    # documented invoice so the built line RESOLVES (the WO-73 fixture
    # precedent; assertions unchanged).
    await register_documented_invoice(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)  # 2026-Q2
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    result = await _submit(db_session, org, claim, txn)
    assert result.status == "submitted"
    await db_session.commit()

    # The client churns; a later annual claim overlaps the locked invoice.
    await lc.set_inactive(db_session, org.id, entity.id)
    annual = await _make_claim(db_session, org, entity, ref_period="2026-YEAR")
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=annual.id,
            invoices=[("Q8", "INV-0001", txn.id)],
            today=date(2027, 1, 5),
            # The annual claim has no lines of its own (the Q2 lines froze
            # with the quarterly claim), so R8 would fire first on the €0
            # preview; the override lets the ORDER assertion reach R44 vs R6.
            override_minimum=True,
        )
    assert exc.value.code == "customer_not_active"  # R44 before R6


@pytest.mark.asyncio
async def test_g2_11_preparation_surfaces_stay_ungated_for_a_non_active_entity(db_session):
    """Claim creation, line building and the checklist all work for a
    PROSPECT — §3.E's gates sit on the status transition, not on
    preparation (the 1A stage exists to describe exactly this claim)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await lc.add_prospect(db_session, org.id, entity.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    assert len(lines) >= 1

    items = await submission_checklist(db_session, org.id, claim.id, today=date(2026, 7, 1))
    assert items  # evaluates, never raises on lifecycle state


@pytest.mark.asyncio
async def test_g2_11_run_close_still_rebuilds_a_pending_entitys_draft_claim(db_session):
    """Prospect/pending entities are NOT excluded from the close — F1
    scopes the rule to legal/claim GATES; the close is preparation
    (WO-73's documented answer to the standing order's question)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await lc.add_prospect(db_session, org.id, entity.id)
    await lc.promote_prospect(db_session, org.id, entity.id)  # pending
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await db_session.commit()

    result = await close.run_close(db_session, org.id, "2026-05")
    assert any(c["claim_id"] == claim.id for c in result["claims"])


@pytest.mark.asyncio
async def test_g2_11_cross_tenant_entity_is_indistinguishable_from_unonboarded(db_session):
    """Org B's entity id through org A's gate refuses EXACTLY like an
    unonboarded entity — zero information from id guessing (§4.4's
    opacity property; the lookup/mutator functions' literal 404 is proven
    in test_g2_11_customer_lifecycle.py)."""
    org_a = await make_org(db_session)
    await enable_transport(db_session, org_a.id)
    org_b = await make_org(db_session, name="Other Org")
    await enable_transport(db_session, org_b.id)
    entity_b = await make_entity(db_session, org_b.id, seed=9)
    await activate_entity(db_session, org_b.id, entity_b.id, "LV")
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lc.enforce_activation(
            db_session, org_a.id, entity_id=entity_b.id, refund_country="LV"
        )
    assert exc.value.code == "customer_not_active"
    assert "'not onboarded'" in exc.value.message
