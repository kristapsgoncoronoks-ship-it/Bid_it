"""G2.7 — the claim status lifecycle 1A->5 (R12, R17), `BA_fleet_fuel.md`
§3.D. See `app.services.transport.status`'s module docstring for the
documented interpretations this file proves (the "caveat"/1C signal, why
`set_status_code` never touches the coarse engine `status` column).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.invoice import Invoice
from app.models.transport.vat_claim import VatRefundClaim
from app.models.vendor import Vendor
from app.services import extraction
from app.services.transport import checklist, claim_lines, fuel_ingest, lock, status, waiver
from app.services.transport import claim as claim_svc
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

PERIOD_ENDED = date(2026, 7, 1)  # the day after 2026-Q2 ends
PERIOD_NOT_ENDED = date(2026, 6, 15)  # mid-2026-Q2


async def _make_claim(db_session, org, entity, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_vendor(db_session, org, *, name: str = "Q8", country: str | None = "LV") -> Vendor:
    vendor = Vendor(org_id=org.id, name=name, country=country)
    db_session.add(vendor)
    await db_session.flush()
    return vendor


async def _make_invoice(db_session, org, vendor, *, number: str) -> Invoice:
    inv = Invoice(
        org_id=org.id,
        vendor_id=vendor.id,
        invoice_number=number,
        issue_date=date(2026, 5, 1),
        currency="EUR",
        subtotal=Decimal("2000.00"),
        tax_amount=Decimal("420.00"),
        total=Decimal("2420.00"),
    )
    db_session.add(inv)
    await db_session.flush()
    return inv


async def _make_txn(
    db_session,
    org,
    entity,
    *,
    supplier: str = "Q8",
    invoice_ref: str | None = "INV-0001",
    net_eur: Decimal = Decimal("2000.00"),
    vat_eur: Decimal = Decimal("420.00"),
    line_seq: int = 1,
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


async def _attach_document(db_session, org, invoice) -> None:
    run = await extraction.record(
        db_session,
        org.id,
        filename="q8-inv-0001.pdf",
        sha256="a" * 64,
        method="pdf-text",
        status="parsed",
    )
    await extraction.link_to_invoice(db_session, org.id, run.id, invoice.id)
    await db_session.commit()


async def _clean_claim(db_session, org, entity):
    """A claim with one documented, above-minimum, resolved invoice line —
    the baseline every scenario below tweaks exactly one thing on. Returns
    (claim, invoice, fuel_transaction)."""
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    await _attach_document(db_session, org, inv)
    return claim, inv, txn


@pytest.mark.asyncio
async def test_g2_7_derive_stage_is_1a_when_an_unresolved_line_exists(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    # No vendor registered at all — resolves to UNMATCHED.
    await _make_txn(db_session, org, entity, invoice_ref="INV-9999")
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    stage = await status.derive_stage(db_session, org.id, claim.id, today=PERIOD_ENDED)
    assert stage == "1A"


@pytest.mark.asyncio
async def test_g2_7_derive_stage_is_1a_when_a_resolved_invoice_has_no_document(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    # Deliberately no document attached — even though the period HAS ended.

    stage = await status.derive_stage(db_session, org.id, claim.id, today=PERIOD_ENDED)
    assert stage == "1A"


@pytest.mark.asyncio
async def test_g2_7_derive_stage_is_1b_when_the_period_has_not_ended(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)

    stage = await status.derive_stage(db_session, org.id, claim.id, today=PERIOD_NOT_ENDED)
    assert stage == "1B"


@pytest.mark.asyncio
async def test_g2_7_derive_stage_is_1c_when_below_the_art17_minimum(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    # Well below the €400 quarterly threshold.
    await _make_txn(db_session, org, entity, net_eur=Decimal("10.00"), vat_eur=Decimal("2.10"))
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    await _attach_document(db_session, org, inv)

    stage = await status.derive_stage(db_session, org.id, claim.id, today=PERIOD_ENDED)
    assert stage == "1C"


@pytest.mark.asyncio
async def test_g2_7_derive_stage_is_1c_when_a_waiver_is_active(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)
    # A genuinely uninvoiced cash supplier, waived — never becomes a line at
    # all, so it cannot be what makes this "1A" instead of "1C".
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="CASHCO",
        invoice_ref="INPUT-CASH",
        net_eur=Decimal("50.00"),
        vat_eur=Decimal("10.50"),
        line_seq=2,
    )
    await db_session.commit()
    await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    stage = await status.derive_stage(db_session, org.id, claim.id, today=PERIOD_ENDED)
    assert stage == "1C"


@pytest.mark.asyncio
async def test_g2_7_derive_stage_is_1e_when_everything_passes(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)

    stage = await status.derive_stage(db_session, org.id, claim.id, today=PERIOD_ENDED)
    assert stage == "1E"


@pytest.mark.asyncio
async def test_g2_7_derive_stage_refuses_a_non_draft_claim(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    claim.status = "submitted"
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await status.derive_stage(db_session, org.id, claim.id)
    assert exc.value.code == "claim_not_draft"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", status.AUTO_CODES)
async def test_g2_7_set_status_code_refuses_every_auto_code(db_session, code):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await status.set_status_code(db_session, org.id, claim.id, code)
    assert exc.value.code == "status_code_system_controlled"
    assert exc.value.status == 409
    assert "system-controlled" in exc.value.message


@pytest.mark.asyncio
async def test_g2_7_set_status_code_refuses_3a_on_an_unlocked_draft_claim(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await status.set_status_code(db_session, org.id, claim.id, "3A")
    assert exc.value.code == "claim_not_submitted"
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_g2_7_set_status_code_refuses_an_unknown_code(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    claim.status = "submitted"
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await status.set_status_code(db_session, org.id, claim.id, "9Z")
    assert exc.value.code == "unknown_status_code"


@pytest.mark.asyncio
async def test_g2_7_set_status_code_2b_with_a_deadline_changes_nothing_else_and_blocks_nothing(
    db_session,
):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim, _inv, txn = await _clean_claim(db_session, org, entity)
    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        today=PERIOD_ENDED,
    )
    assert result.status == "submitted"

    deadline = date(2026, 9, 1)
    updated = await status.set_status_code(
        db_session, org.id, claim.id, "2B", action_deadline=deadline
    )
    await db_session.commit()
    assert updated.status_code == "2B"
    assert updated.action_deadline == deadline
    assert updated.status == "submitted", "R12 — a 2B reminder changes no status"

    # "blocks nothing" — a subsequent legal action (withdraw) still works.
    withdrawn = await lock.withdraw_claim(db_session, org.id, claim.id)
    assert withdrawn.status == "withdrawn"


@pytest.mark.asyncio
async def test_g2_7_submit_claim_stamps_status_code_2(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim, _inv, txn = await _clean_claim(db_session, org, entity)

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        today=PERIOD_ENDED,
    )
    assert result.status_code == "2"

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status_code == "2"


@pytest.mark.asyncio
async def test_g2_7_derive_stage_is_1a_when_the_claimant_entity_has_no_iban(db_session):
    """G2.10 slice 1 (WO-60): derive_stage now consults the adjustable
    `bank_account` checklist rule, not just the two WO-59 proxy checks."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    entity.iban = None
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)

    stage = await status.derive_stage(db_session, org.id, claim.id, today=PERIOD_ENDED)
    assert stage == "1A"


@pytest.mark.asyncio
async def test_g2_7_deactivating_bank_account_rule_unblocks_derive_stage(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    entity.iban = None
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)
    assert await status.derive_stage(db_session, org.id, claim.id, today=PERIOD_ENDED) == "1A"

    await checklist.set_active(db_session, org.id, "bank_account", False)
    await db_session.commit()

    assert await status.derive_stage(db_session, org.id, claim.id, today=PERIOD_ENDED) == "1E"
