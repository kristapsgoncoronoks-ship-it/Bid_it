"""WO-75 — R3 as a LOCK-GATE consumer: `lock.submit_claim` refuses a claim
whose materialized (unfrozen) lines include ANY synthetic line, via THE one
`claim_gates.is_synthetic()` predicate (`BA_fleet_fuel.md` C2: the lock gate's
`bad = [... if _synthetic(r)]` -> "BLOCKED - unresolved invoice refs"; C9's
internal order puts the `bad` gate before `claim_set`/locks/doc-gate/freeze).
This file proves the refusal, its POSITION in D5's engine-gate group (before
R6 and R10), the D5 nothing-mutated guarantee, and the R15 interaction (a
waived supplier's synthetic transactions never became lines, so waiving stays
the legitimate path past this gate for a genuinely-uninvoiced supplier).
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.invoice import Invoice
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.models.vendor import Vendor
from app.services.transport import claim as claim_svc
from app.services.transport import claim_gates, claim_lines, fuel_ingest, lock, waiver
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import (
    activate_entity,
    enable_transport,
    make_entity,
    make_org,
    register_documented_invoice,
)

TODAY = date(2026, 7, 1)  # the day after 2026-Q2's period end — the R7 seam


async def _make_claim(db_session, org, entity, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_txn(
    db_session,
    org,
    entity,
    *,
    supplier: str = "Q8",
    invoice_ref: str | None = "INV-0001",
    line_seq: int = 1,
    net_eur: Decimal = Decimal("2000.00"),
    vat_eur: Decimal = Decimal("420.00"),
):
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
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        invoice_ref=invoice_ref,
    )


async def _ready_org(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await activate_entity(db_session, org.id, entity.id, "LV")
    await db_session.commit()
    return org, entity


async def _assert_nothing_mutated(db_session, claim_id: str) -> None:
    """The D5 proof pattern: a refused submit leaves the claim exactly as it
    was — `draft`, no workflow code, no frozen base, zero frozen lines,
    zero lock rows."""
    db_session.expire_all()
    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim_id))
    assert fresh.status == "draft"
    assert fresh.status_code is None
    assert fresh.vat_eur is None
    frozen = list(
        await db_session.scalars(
            select(VatRefundClaimLine).where(
                VatRefundClaimLine.claim_id == claim_id,
                VatRefundClaimLine.frozen_at.is_not(None),
            )
        )
    )
    assert frozen == []
    locks = list(
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == claim_id)
        )
    )
    assert locks == []


# ---------------------------------------------------------------------------
# The refusal itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r3_submit_refuses_a_claim_with_an_unmatched_line_nothing_mutated(db_session):
    """C2 verbatim, on the SUBMIT surface: no vendor/invoice registered, so
    the built line is `UNMATCHED` — the claim must never freeze or lock."""
    org, entity = await _ready_org(db_session)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)  # no registered invoice -> UNMATCHED
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    claim_id = claim.id
    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim_id,
            invoices=[("Q8", "INV-0001", txn.id)],
            today=TODAY,
        )
    assert exc.value.code == "unresolved_invoice_refs"
    assert exc.value.status == 409
    assert "UNMATCHED" in exc.value.message
    await db_session.rollback()
    await _assert_nothing_mutated(db_session, claim_id)


@pytest.mark.asyncio
async def test_r3_submit_refuses_an_input_vat_id_on_an_otherwise_resolved_line(db_session):
    """The predicate's SECOND input is live at submit too: a real, resolved,
    documented line whose `vat_id` carries the `INPUT` placeholder is still
    synthetic (C2 clause 4)."""
    org, entity = await _ready_org(db_session)
    await register_documented_invoice(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    line = await db_session.scalar(
        select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
    )
    line.vat_id = "INPUT"
    await db_session.commit()

    claim_id = claim.id
    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim_id,
            invoices=[("Q8", "INV-0001", txn.id)],
            today=TODAY,
        )
    assert exc.value.code == "unresolved_invoice_refs"
    await db_session.rollback()
    await _assert_nothing_mutated(db_session, claim_id)


# ---------------------------------------------------------------------------
# Position in the D5 engine-gate group (C9: bad -> claim_set/locks -> doc-gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r3_gate_precedes_the_duplicate_block(db_session):
    """A quarterly claim that BOTH overlaps an existing lock (R6) AND carries
    an UNMATCHED line refuses with the R3 code — C9 puts the `bad` gate
    before `claim_set`/locks."""
    org, entity = await _ready_org(db_session)
    await register_documented_invoice(db_session, org.id)
    claim_a = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn_a = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim_a.id)
    await db_session.commit()
    await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim_a.id,
        invoices=[("Q8", "INV-0001", txn_a.id)],
        today=TODAY,
    )
    await db_session.commit()

    # Claim B (Q3): one UNMATCHED line of its own + a caller-supplied overlap
    # with claim A's already-locked invoice key.
    claim_b = await _make_claim(db_session, org, entity, ref_period="2026-Q3")
    await db_session.commit()
    txn_b = await _make_txn(
        db_session,
        org,
        entity,
        supplier="CASHCO",
        invoice_ref="NO-SUCH-REF",
        line_seq=2,
    )
    # Land the transaction in Q3's months so build_claim_lines picks it up.
    txn_b.period = "2026-08"
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim_b.id)
    await db_session.commit()

    claim_b_id = claim_b.id
    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim_b_id,
            invoices=[("Q8", "INV-0001", txn_b.id)],  # would be R6's duplicate
            today=date(2026, 10, 1),
        )
    assert exc.value.code == "unresolved_invoice_refs", "R3 must fire before R6"
    await db_session.rollback()
    await _assert_nothing_mutated(db_session, claim_b_id)


@pytest.mark.asyncio
async def test_r3_gate_precedes_the_document_gate(db_session):
    """A claim with BOTH an undocumented resolved line (R10) AND an UNMATCHED
    line refuses with the R3 code — C9 puts the `bad` gate before the
    doc-gate."""
    org, entity = await _ready_org(db_session)
    # Registered invoice WITHOUT a vaulted document -> the resolved line
    # would trip R10 if reached.
    vendor = Vendor(org_id=org.id, name="Q8", country="LV")
    db_session.add(vendor)
    await db_session.flush()
    db_session.add(
        Invoice(
            org_id=org.id,
            vendor_id=vendor.id,
            invoice_number="INV-0001",
            issue_date=date(2026, 5, 1),
            currency="EUR",
            subtotal=Decimal("2000.00"),
            tax_amount=Decimal("420.00"),
            total=Decimal("2420.00"),
        )
    )
    await db_session.flush()
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)  # resolves, undocumented
    await _make_txn(
        db_session, org, entity, supplier="CASHCO", invoice_ref="NO-SUCH-REF", line_seq=2
    )  # UNMATCHED
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    claim_id = claim.id
    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim_id,
            invoices=[("Q8", "INV-0001", txn.id)],
            today=TODAY,
        )
    assert exc.value.code == "unresolved_invoice_refs", "R3 must fire before R10"
    await db_session.rollback()
    await _assert_nothing_mutated(db_session, claim_id)


# ---------------------------------------------------------------------------
# The legitimate paths past the gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r3_fully_waived_supplier_never_trips_the_gate(db_session):
    """R15 interaction (WO-58): a genuinely-uninvoiced supplier's synthetic
    `INPUT` transactions are excluded from the claim BY CONSTRUCTION once
    waived — they never become lines, so the R3 gate sees nothing and the
    claim (carried by a resolved, documented supplier) submits."""
    org, entity = await _ready_org(db_session)
    await register_documented_invoice(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="CASHCO",
        invoice_ref="INPUT:pending",
        line_seq=2,
        net_eur=Decimal("100.00"),
        vat_eur=Decimal("21.00"),
    )
    await db_session.commit()
    await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    assert await claim_gates.unfrozen_synthetic_refs(db_session, org.id, claim.id) == []

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        today=TODAY,
    )
    assert result.status == "submitted"
    assert "CASHCO" in (result.status_note or "")


@pytest.mark.asyncio
async def test_r3_claim_with_no_materialized_lines_passes_trivially(db_session):
    """Same semantics as R10: no materialized lines means nothing to scan —
    the gate passes and the submission proceeds on the caller-supplied
    invoice tuples (the R8 minimum separately refuses the zero VAT base
    unless overridden, which this test does explicitly)."""
    org, entity = await _ready_org(db_session)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    # Deliberately NO build_claim_lines call.

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        today=TODAY,
        override_minimum=True,
    )
    assert result.status == "submitted"


# ---------------------------------------------------------------------------
# The scan helper itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r3_unfrozen_synthetic_refs_is_sorted_distinct_and_org_scoped(db_session):
    org, entity = await _ready_org(db_session)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    # Two UNMATCHED-producing suppliers -> ONE collapsed UNMATCHED line
    # (distinct suppliers share the same terminal ref and product_group).
    await _make_txn(db_session, org, entity, supplier="CASHCO", invoice_ref="X-1", line_seq=1)
    await _make_txn(db_session, org, entity, supplier="NOWHERE", invoice_ref="Y-2", line_seq=2)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    refs = await claim_gates.unfrozen_synthetic_refs(db_session, org.id, claim.id)
    assert refs == ["UNMATCHED"], "distinct — the collapsed ref appears once"

    # Org-scoped: another org never sees this claim's lines (§4.4 — the
    # submit path 404s on the cross-tenant claim id first regardless).
    other = await make_org(db_session, name="Other Org")
    assert await claim_gates.unfrozen_synthetic_refs(db_session, other.id, claim.id) == []


@pytest.mark.asyncio
async def test_r3_enforce_names_every_offender(db_session):
    """One pass, all offenders named (the R6/R10 message discipline): an
    UNMATCHED line and an INPUT-ref line are both listed."""
    org, entity = await _ready_org(db_session)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity, supplier="CASHCO", invoice_ref="X-1", line_seq=1)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    # Tamper a second synthetic shape directly onto the materialized line set
    # so both clauses appear at once.
    line = await db_session.scalar(
        select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
    )
    db_session.add(
        VatRefundClaimLine(
            org_id=org.id,
            claim_id=claim.id,
            invoice_ref="INPUT:pending",
            invoice_id=None,
            product_group=line.product_group,
            net_eur=Decimal("1.00"),
            vat_eur=Decimal("0.21"),
            net_local=Decimal("1.00"),
            vat_local=Decimal("0.21"),
            currency="EUR",
            goods_code="1",
        )
    )
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await claim_gates.enforce_no_synthetic_lines(db_session, org.id, claim.id)
    assert exc.value.code == "unresolved_invoice_refs"
    assert "UNMATCHED" in exc.value.message
    assert "INPUT:pending" in exc.value.message


# ---------------------------------------------------------------------------
# Structural (R3 — one predicate, no rival)
# ---------------------------------------------------------------------------


def test_r3_lock_module_defines_no_rival_predicate():
    """`lock.py` consumes `claim_gates.enforce_no_synthetic_lines` and never
    re-derives the synthetic pattern itself (the WO-51 grep-test style —
    the drift R3 exists to prevent)."""
    source = inspect.getsource(lock)
    assert "enforce_no_synthetic_lines" in source
    for literal in ('"INPUT"', "'INPUT'", '"ALL:"', "'ALL:'", '"UNMATCHED"', "'UNMATCHED'"):
        assert literal not in source, f"rival synthetic-pattern literal {literal} in lock.py"


def test_r3_gate_order_r3_sits_between_activation_and_duplicate_block():
    """The harvested position, asserted structurally: R44 (enforce_activation)
    -> R3 (enforce_no_synthetic_lines) -> R6 (_apply_annual_mop_up_or_
    duplicate_block) inside `submit_claim`'s body (D5 + C9)."""
    source = inspect.getsource(lock.submit_claim)
    i_activation = source.index("enforce_activation(")
    i_synthetic = source.index("enforce_no_synthetic_lines(")
    i_duplicate = source.index("_apply_annual_mop_up_or_duplicate_block(")
    assert i_activation < i_synthetic < i_duplicate
