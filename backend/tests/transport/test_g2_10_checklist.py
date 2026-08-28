"""G2.10 — the adjustable submission checklist as DATA (R45),
`BA_fleet_fuel.md` §3.E. See `app.services.transport.checklist`'s module
docstring for why the receipt-control/unresolved-refs split re-queries
`fuel_transactions` rather than reading the collapsed `vat_claim_lines` ref.

This file covers slice 1's own subject — the DATA verifiers, the four
claim-level items and the seed's idempotency. Slice 2's four added rules
(the document checks, country scope, NACE) have their own file,
`test_wo_ab_claimant_documents.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice
from app.models.transport.checklist_rule import VatChecklistRule
from app.models.vendor import Vendor
from app.services import extraction
from app.services.transport import checklist, claim_lines, fuel_ingest, waiver
from app.services.transport import claim as claim_svc
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

PERIOD_ENDED = date(2026, 7, 1)


async def _make_claim(db_session, org, entity, **overrides):
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


# §3.E's rule table, verbatim — the assertion is the HARVEST, so a rule
# quietly dropped from `DEFAULT_RULES` fails here rather than becoming a
# checklist that no longer checks something the spec requires.
HARVESTED_RULES = {
    "contract",
    "customer_data",
    "bank_account",
    "nace",
    "trade_register",
    "power_of_attorney",
}


@pytest.mark.asyncio
async def test_g2_10_seed_default_rules_is_idempotent_and_seeds_the_whole_harvest(db_session):
    """WO-AB: slice 1 seeded two of the six and said why. Slice 2 seeds all
    six, so this asserts §3.E's table rather than a slice's subset."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)

    created = await checklist.seed_default_rules(db_session, org.id)
    await db_session.commit()
    assert {r.key for r in created} == HARVESTED_RULES
    assert all(r.active for r in created)

    again = await checklist.seed_default_rules(db_session, org.id)
    await db_session.commit()
    assert again == []

    rows = (
        await db_session.scalars(select(VatChecklistRule).where(VatChecklistRule.org_id == org.id))
    ).all()
    assert len(rows) == len(HARVESTED_RULES)


@pytest.mark.asyncio
async def test_g2_10_customer_data_verifier_fails_naming_the_missing_field(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    entity.registration_number = None
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)

    items = await checklist.submission_checklist(db_session, org.id, claim.id, today=PERIOD_ENDED)
    by_key = {i.key: i for i in items}
    assert by_key["customer_data"].ok is False
    assert "registration number" in by_key["customer_data"].reason


@pytest.mark.asyncio
async def test_g2_10_bank_account_verifier_fails_with_no_iban(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    entity.iban = None
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)

    items = await checklist.submission_checklist(db_session, org.id, claim.id, today=PERIOD_ENDED)
    by_key = {i.key: i for i in items}
    assert by_key["bank_account"].ok is False
    assert "IBAN" in by_key["bank_account"].reason


@pytest.mark.asyncio
async def test_g2_10_deactivating_a_rule_removes_it_from_the_gate(db_session):
    """R45's own acceptance test, verbatim: deactivate a rule ⇒ it
    disappears from the gate."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    entity.iban = None  # would otherwise fail bank_account
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)

    items = await checklist.submission_checklist(db_session, org.id, claim.id, today=PERIOD_ENDED)
    assert any(i.key == "bank_account" and not i.ok for i in items)

    await checklist.set_active(db_session, org.id, "bank_account", False)
    await db_session.commit()

    items_after = await checklist.submission_checklist(
        db_session, org.id, claim.id, today=PERIOD_ENDED
    )
    assert all(i.key != "bank_account" for i in items_after), (
        "deactivated rule must disappear entirely"
    )


@pytest.mark.asyncio
async def test_g2_10_receipt_control_names_a_genuinely_uninvoiced_supplier(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)
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

    items = await checklist.submission_checklist(db_session, org.id, claim.id, today=PERIOD_ENDED)
    by_key = {i.key: i for i in items}
    assert by_key["receipt_control"].ok is False
    assert "CASHCO" in by_key["receipt_control"].reason
    assert by_key["unresolved_refs"].ok is True, (
        "a waivable supplier must not block unresolved_refs"
    )

    # Waiving it clears the receipt-control item entirely.
    await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    await db_session.commit()
    items_after = await checklist.submission_checklist(
        db_session, org.id, claim.id, today=PERIOD_ENDED
    )
    by_key_after = {i.key: i for i in items_after}
    assert by_key_after["receipt_control"].ok is True


@pytest.mark.asyncio
async def test_g2_10_unresolved_refs_blocks_when_the_supplier_has_a_registered_invoice(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org, name="DKV")
    await _make_invoice(db_session, org, vendor, number="DKV-0001")
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)
    # A DKV transaction whose note does NOT match DKV-0001 and DKV has 2
    # registered invoices, so the sole-fallback heuristic cannot resolve it.
    await _make_invoice(db_session, org, vendor, number="DKV-0002")
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="DKV",
        invoice_ref="SOMETHING-ELSE",
        net_eur=Decimal("50.00"),
        vat_eur=Decimal("10.50"),
        line_seq=2,
    )
    await db_session.commit()

    items = await checklist.submission_checklist(db_session, org.id, claim.id, today=PERIOD_ENDED)
    by_key = {i.key: i for i in items}
    assert by_key["unresolved_refs"].ok is False
    assert "DKV" in by_key["unresolved_refs"].reason
    assert by_key["receipt_control"].ok is True, (
        "a non-waivable supplier must not block receipt_control"
    )

    # Attempting to waive DKV is still refused (unchanged from WO-58).
    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="DKV")
    assert exc.value.code == "waiver_supplier_has_invoices"


@pytest.mark.asyncio
async def test_g2_10_documents_and_period_items_reuse_existing_pure_checks(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim, _inv, _txn = await _clean_claim(db_session, org, entity)

    items = await checklist.submission_checklist(db_session, org.id, claim.id, today=PERIOD_ENDED)
    by_key = {i.key: i for i in items}
    assert by_key["documents_attached"].ok is True
    assert by_key["period_ended"].ok is True

    items_mid_period = await checklist.submission_checklist(
        db_session, org.id, claim.id, today=date(2026, 6, 15)
    )
    by_key_mid = {i.key: i for i in items_mid_period}
    assert by_key_mid["period_ended"].ok is False
