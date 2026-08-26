"""WO-L — claim hygiene: the three owner-decided 2026-08-08 items.

- **§12 `ignored`**: a detected/packaged overcharge claim-back can be
  explicitly ignored WITH a reason (audited), reinstated back to detected,
  and never ignored once a demand went out (`claimed` keeps the harvested
  three outcomes only). An ignored row books nothing.
- **§11 supplier list**: an UNMATCHED claim line carries the distinct
  suppliers behind it (option b — grain unchanged, work-item hint); a
  resolved line carries none.
- **§13 decision**: submitted → approved/rejected, and PARTIAL rejection
  stamps the named frozen lines, shrinks the claim's frozen base and
  recomputes the fee at the FROZEN rate (fee.py's documented seam) —
  including the minimum-floor branch. Every refusal has its own code.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.models.vendor import Vendor
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, decision, fuel_ingest, overcharge
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

pytestmark = pytest.mark.asyncio


async def _make_txn(db_session, org, entity, *, supplier, invoice_ref, line_seq, vat=21):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier=supplier,
        period="2026-05",
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 15),
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=Decimal("100.00"),
        vat_local=Decimal(vat),
        gross_local=Decimal("100.00") + Decimal(vat),
        net_eur=Decimal("100.00"),
        vat_eur=Decimal(vat),
        invoice_ref=invoice_ref,
    )


# --------------------------------------------------------------------------- #
# §12 — the audited ignore
# --------------------------------------------------------------------------- #


async def _detected_claimback(db_session, org):
    """A claim-back at `detected`, constructed directly — `open_claim` runs
    the whole contract audit, which is proven in the WO-82 suite; this suite
    tests the state machine AFTER detection."""
    from app.models.transport.overcharge import VatOverchargeClaim

    row = VatOverchargeClaim(
        org_id=org.id,
        supplier="Q8",
        period="2026-05",
        status="detected",
        detected_eur=Decimal("120.00"),
        lines_count=3,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def test_ignore_needs_a_reason_and_reinstate_returns_to_detected(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id, fee_rate=False)
    row = await _detected_claimback(db_session, org)
    await db_session.commit()

    # No reason → refused with its own code.
    with pytest.raises(AppError) as exc:
        await overcharge.advance_claim(db_session, org.id, row.id, to_status="ignored")
    assert exc.value.code == "ignore_reason_required"

    # With a reason it lands, audited.
    row = await overcharge.advance_claim(
        db_session, org.id, row.id, to_status="ignored", note="Gap is 12 EUR — not worth the chase"
    )
    assert row.status == "ignored"

    # Reinstate: back to detected, the only edge out.
    row = await overcharge.advance_claim(db_session, org.id, row.id, to_status="detected")
    assert row.status == "detected"


async def test_a_sent_demand_cannot_be_ignored(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id, fee_rate=False)
    row = await _detected_claimback(db_session, org)
    await db_session.commit()
    await overcharge.advance_claim(db_session, org.id, row.id, to_status="packaged")
    await overcharge.advance_claim(db_session, org.id, row.id, to_status="claimed")
    with pytest.raises(AppError) as exc:
        await overcharge.advance_claim(
            db_session, org.id, row.id, to_status="ignored", note="changed my mind"
        )
    assert exc.value.code == "overcharge_transition_invalid"


async def test_ignored_books_no_cash(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id, fee_rate=False)
    row = await _detected_claimback(db_session, org)
    await db_session.commit()
    await overcharge.advance_claim(
        db_session, org.id, row.id, to_status="ignored", note="mis-keyed term"
    )
    total = await overcharge.recovered_total(db_session, org.id)
    assert total == Decimal("0.00")


# --------------------------------------------------------------------------- #
# §11 — the UNMATCHED supplier list
# --------------------------------------------------------------------------- #


async def test_unmatched_line_carries_its_suppliers_and_resolved_lines_do_not(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id, fee_rate=False)
    entity = await make_entity(db_session, org.id)
    claim = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2026-Q2"
    )
    await db_session.commit()

    # Two suppliers whose refs resolve to NO registered invoice → one
    # UNMATCHED diesel bucket spanning both.
    await _make_txn(db_session, org, entity, supplier="Q8", invoice_ref="RAW-1", line_seq=1)
    await _make_txn(db_session, org, entity, supplier="BP", invoice_ref="RAW-2", line_seq=2)
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    unmatched = [ln for ln in lines if ln.invoice_ref == claim_lines.UNMATCHED]
    assert len(unmatched) == 1
    assert json.loads(unmatched[0].unmatched_suppliers) == ["BP", "Q8"]  # sorted, distinct

    # A resolved line never carries the list: register Q8's invoice, rebuild.
    vendor = Vendor(org_id=org.id, name="Q8", country="LV")
    db_session.add(vendor)
    await db_session.flush()
    from app.models.invoice import Invoice

    inv = Invoice(
        org_id=org.id,
        vendor_id=vendor.id,
        invoice_number="RAW-1",
        issue_date=date(2026, 5, 1),
        currency="EUR",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("21.00"),
        total=Decimal("121.00"),
    )
    db_session.add(inv)
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    resolved = [ln for ln in lines if ln.invoice_ref == "RAW-1"]
    assert resolved and resolved[0].unmatched_suppliers is None
    still_unmatched = [ln for ln in lines if ln.invoice_ref == claim_lines.UNMATCHED]
    assert json.loads(still_unmatched[0].unmatched_suppliers) == ["BP"]


# --------------------------------------------------------------------------- #
# §13 — the decision transition
# --------------------------------------------------------------------------- #


async def _submitted_claim(db_session, org, entity, *, fee_min=Decimal("25.00")) -> VatRefundClaim:
    """A claim in the post-submit shape: frozen lines + frozen figures. Built
    directly (the WO-77 status-test precedent) — the submit gate chain is
    proven elsewhere; this suite tests what happens AFTER it."""
    claim = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2026-Q2"
    )
    frozen = datetime.now(UTC)
    for ref, vat in [("INV-A", "210.00"), ("INV-B", "90.00"), ("INV-C", "60.00")]:
        db_session.add(
            VatRefundClaimLine(
                org_id=org.id,
                claim_id=claim.id,
                invoice_ref=ref,
                product_group="DIESEL",
                goods_code="1",
                net_eur=Decimal("1000.00"),
                vat_eur=Decimal(vat),
                net_local=Decimal("1000.00"),
                vat_local=Decimal(vat),
                currency="EUR",
                frozen_at=frozen,
            )
        )
    claim.status = "submitted"
    claim.vat_eur = Decimal("360.00")
    claim.vat_local = Decimal("360.00")
    claim.currency = "EUR"
    claim.fee_pct = Decimal("10.00")
    claim.fee_min = fee_min
    claim.fee_eur = Decimal("36.00")
    await db_session.commit()
    return claim


async def test_full_approval_and_full_rejection_leave_figures_frozen(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _submitted_claim(db_session, org, entity)

    out = await decision.record_decision(
        db_session, org.id, claim.id, outcome="approved", decision_date=date(2026, 8, 1)
    )
    assert (out.status, out.approved_date, out.decision_date) == (
        "approved",
        date(2026, 8, 1),
        date(2026, 8, 1),
    )
    assert (out.vat_eur, out.fee_eur) == (Decimal("360.00"), Decimal("36.00"))

    # A decided claim refuses a second decision.
    with pytest.raises(AppError) as exc:
        await decision.record_decision(db_session, org.id, claim.id, outcome="rejected")
    assert exc.value.code == "claim_not_awaiting_decision"


async def test_partial_rejection_shrinks_the_base_at_the_frozen_rate(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _submitted_claim(db_session, org, entity)

    out = await decision.record_decision(
        db_session, org.id, claim.id, outcome="partial", rejected_refs=["INV-B"]
    )
    # 360 − 90 = 270; fee = 10% of 270 at the FROZEN rate = 27.00 (above min).
    assert out.status == "approved"
    assert out.vat_eur == Decimal("270.00")
    assert out.vat_local == Decimal("270.00")
    assert out.fee_eur == Decimal("27.00")

    lines = await claim_lines.list_claim_lines(db_session, org.id, claim.id)
    stamped = {ln.invoice_ref: ln.rejected_at for ln in lines}
    assert stamped["INV-B"] is not None
    assert stamped["INV-A"] is None and stamped["INV-C"] is None


async def test_partial_rejection_hits_the_frozen_minimum_floor(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _submitted_claim(db_session, org, entity, fee_min=Decimal("25.00"))

    # Reject A and B: base 60.00 → 10% = 6.00, floored to the FROZEN 25.00 min.
    out = await decision.record_decision(
        db_session, org.id, claim.id, outcome="partial", rejected_refs=["INV-A", "INV-B"]
    )
    assert out.vat_eur == Decimal("60.00")
    assert out.fee_eur == Decimal("25.00")


async def test_decision_refusals_have_their_own_codes(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _submitted_claim(db_session, org, entity)

    with pytest.raises(AppError) as exc:
        await decision.record_decision(db_session, org.id, claim.id, outcome="maybe")
    assert exc.value.code == "invalid_decision_outcome"

    with pytest.raises(AppError) as exc:
        await decision.record_decision(
            db_session, org.id, claim.id, outcome="approved", rejected_refs=["INV-A"]
        )
    assert exc.value.code == "rejected_refs_not_applicable"

    with pytest.raises(AppError) as exc:
        await decision.record_decision(
            db_session, org.id, claim.id, outcome="partial", rejected_refs=["INV-NOPE"]
        )
    assert exc.value.code == "rejected_refs_unknown"

    with pytest.raises(AppError) as exc:
        await decision.record_decision(
            db_session,
            org.id,
            claim.id,
            outcome="partial",
            rejected_refs=["INV-A", "INV-B", "INV-C"],
        )
    assert exc.value.code == "partial_rejects_everything"

    with pytest.raises(AppError) as exc:
        await decision.record_decision(db_session, org.id, claim.id, outcome="partial")
    assert exc.value.code == "rejected_refs_required"

    # Still undecided after five refusals — nothing mutated.
    fresh = await claim_svc.get_claim(db_session, org.id, claim.id)
    assert fresh.status == "submitted"
    assert fresh.vat_eur == Decimal("360.00")
