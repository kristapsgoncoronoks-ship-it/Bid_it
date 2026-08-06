"""G2.6 slice 3 — the document-presence gate (R10), `BA_fleet_fuel.md` C8:
"Every invoice in the claim set must have >=1 vaulted document, else BLOCKED
- physical document missing." See `app.services.transport.document_gate`'s
module docstring for why this reads the claim's own MATERIALIZED lines
(never `submit_claim`'s caller-supplied `invoices` tuple list) and why an
`UNMATCHED` line is invisible to this gate.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.invoice import Invoice
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.models.vendor import Vendor
from app.services import extraction
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, document_gate, fuel_ingest, lock
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import activate_entity, enable_transport, make_entity, make_org


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
):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier=supplier,
        period="2026-05",
        line_seq=1,
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


async def _attach_document(db_session, org, invoice, *, sha256: str | None = "a" * 64) -> None:
    run = await extraction.record(
        db_session,
        org.id,
        filename="q8-inv-0001.pdf",
        sha256=sha256,
        method="pdf-text",
        status="parsed",
    )
    await extraction.link_to_invoice(db_session, org.id, run.id, invoice.id)
    await db_session.commit()


@pytest.mark.asyncio
async def test_g2_6_a_resolved_line_with_no_linked_document_blocks(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await document_gate.enforce_document_presence(db_session, org.id, claim.id)
    assert exc.value.code == "invoice_document_missing"
    assert exc.value.status == 409
    assert "INV-0001" in exc.value.message
    assert inv.id  # sanity — the invoice exists, just has no document


@pytest.mark.asyncio
async def test_g2_6_attaching_a_real_document_clears_the_gate(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    await _attach_document(db_session, org, inv)

    await document_gate.enforce_document_presence(db_session, org.id, claim.id)  # no raise


@pytest.mark.asyncio
async def test_g2_6_a_capture_with_no_real_bytes_does_not_count_as_a_document(db_session):
    """A manually-typed invoice's capture run with a NULL `source_sha256` is
    not "vaulted" — the gate must still treat the invoice as missing a
    document."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    await _attach_document(db_session, org, inv, sha256=None)

    with pytest.raises(AppError) as exc:
        await document_gate.enforce_document_presence(db_session, org.id, claim.id)
    assert exc.value.code == "invoice_document_missing"


@pytest.mark.asyncio
async def test_g2_6_an_unmatched_line_is_invisible_to_the_gate(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    # No vendor registered at all — the transaction resolves to UNMATCHED.
    await _make_txn(db_session, org, entity, invoice_ref="INV-9999")
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    assert lines[0].invoice_ref == "UNMATCHED"
    assert lines[0].invoice_id is None

    await document_gate.enforce_document_presence(db_session, org.id, claim.id)  # no raise


@pytest.mark.asyncio
async def test_g2_6_submit_claim_refuses_when_the_locked_invoice_has_no_document(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    # WO-73 (R44): raised past the activation gate — the WO-60 fixture precedent.
    await activate_entity(db_session, org.id, entity.id, "LV")
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim.id,
            invoices=[("Q8", "INV-0001", txn.id)],
            today=date(2026, 7, 1),
        )
    assert exc.value.code == "invoice_document_missing"
    assert exc.value.status == 409

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "draft"
    assert fresh.vat_eur is None, "a document-missing claim must never freeze"
    lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert all(ln.frozen_at is None for ln in lines)

    # Attach the document — the exact same submission now succeeds.
    await _attach_document(db_session, org, inv)
    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        today=date(2026, 7, 1),
    )
    assert result.status == "submitted"
    assert result.status_code == "2", "G2.7 (WO-59) — submit_claim stamps the workflow code too"
