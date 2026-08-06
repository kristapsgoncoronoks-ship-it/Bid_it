"""G2.6 slice 3 — receipt-control waivers (R15), `BA_fleet_fuel.md` C9, rule
"R5 case (a)": "Receipt-control waivers are permitted ONLY for a genuinely
uninvoiced supplier (synthetic INPUT ref AND no registered invoice for that
country). Waiving a supplier that HAS invoices is REFUSED." See
`app.services.transport.waiver`'s module docstring for the full design.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.invoice import Invoice
from app.models.transport.receipt_waiver import VatReceiptWaiver
from app.models.transport.vat_claim import VatRefundClaim
from app.models.vendor import Vendor
from app.services import extraction
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, fuel_ingest, lock, waiver
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import activate_entity, enable_transport, make_entity, make_org


async def _make_claim(db_session, org, entity, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_vendor(db_session, org, *, name: str, country: str | None = "LV") -> Vendor:
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
    supplier: str,
    invoice_ref: str | None,
    net_eur: Decimal,
    vat_eur: Decimal,
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


@pytest.mark.asyncio
async def test_g2_6_set_waiver_succeeds_for_a_genuinely_uninvoiced_supplier(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    row = await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    await db_session.commit()
    assert isinstance(row, VatReceiptWaiver)
    assert row.supplier == "CASHCO"
    assert row.claim_id == claim.id


@pytest.mark.asyncio
async def test_g2_6_set_waiver_refuses_a_supplier_with_a_registered_invoice(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org, name="DKV")
    await _make_invoice(db_session, org, vendor, number="DKV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="DKV")
    assert exc.value.code == "waiver_supplier_has_invoices"
    assert exc.value.status == 422
    assert "note-matching fix" in exc.value.message


@pytest.mark.asyncio
async def test_g2_6_set_waiver_is_idempotent(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    first = await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    await db_session.commit()
    second = await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    await db_session.commit()
    assert first.id == second.id

    rows = (
        await db_session.scalars(
            select(VatReceiptWaiver).where(VatReceiptWaiver.claim_id == claim.id)
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_g2_6_set_and_remove_waiver_refuse_on_a_non_draft_claim(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    claim.status = "submitted"
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    assert exc.value.code == "claim_not_draft"
    assert exc.value.status == 409

    with pytest.raises(AppError) as exc2:
        await waiver.remove_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    assert exc2.value.code == "claim_not_draft"


@pytest.mark.asyncio
async def test_g2_6_remove_waiver_un_waives_and_is_a_no_op_the_second_time(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await waiver.set_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    await db_session.commit()

    removed = await waiver.remove_waiver(db_session, org.id, claim_id=claim.id, supplier="CASHCO")
    await db_session.commit()
    assert removed is True

    removed_again = await waiver.remove_waiver(
        db_session, org.id, claim_id=claim.id, supplier="CASHCO"
    )
    assert removed_again is False


@pytest.mark.asyncio
async def test_g2_6_submit_claim_end_to_end_excludes_the_waived_supplier_and_stamps_status_note(
    db_session,
):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    # WO-73 (R44): raised past the activation gate — the WO-60 fixture precedent.
    await activate_entity(db_session, org.id, entity.id, "LV")
    vendor = await _make_vendor(db_session, org, name="Q8")
    inv = await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    txn_q8 = await _make_txn(
        db_session,
        org,
        entity,
        supplier="Q8",
        invoice_ref="INV-0001",
        net_eur=Decimal("2000.00"),
        vat_eur=Decimal("420.00"),
        line_seq=1,
    )
    # A genuinely uninvoiced cash supplier — no vendor registered at all.
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

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    assert len(lines) == 1, "the waived supplier's transaction must produce ZERO claim lines"
    assert lines[0].invoice_ref == "INV-0001"

    await _attach_document(db_session, org, inv)

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn_q8.id)],
        today=date(2026, 7, 1),
    )
    assert result.status == "submitted"
    assert result.vat_eur == Decimal("420.00"), "the waived supplier's VAT must never be counted"
    assert result.status_note is not None
    assert "Receipt-control waiver applied" in result.status_note
    assert "CASHCO" in result.status_note
