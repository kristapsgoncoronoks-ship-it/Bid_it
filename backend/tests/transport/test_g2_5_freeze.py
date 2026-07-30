"""G2.5 — frozen claim lines + frozen VAT base at submission (`BA_fleet_fuel.md`
C10, ADR-P3). See `app.services.transport.freeze`'s module docstring for why
this is "the linchpin" and why a mixed-currency claim refuses to freeze.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, freeze, fuel_ingest, lock
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _make_claim(db_session, org, entity, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_txn(
    db_session,
    org,
    entity,
    *,
    supplier="Q8",
    invoice_ref="INV-0001",
    line_seq=1,
    period="2026-05",
    currency="EUR",
    net_eur=Decimal("100.00"),
    vat_eur=Decimal("21.00"),
    net_local=None,
    vat_local=None,
):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 15),
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency=currency,
        net_local=net_local if net_local is not None else net_eur,
        vat_local=vat_local if vat_local is not None else vat_eur,
        gross_local=(net_local if net_local is not None else net_eur)
        + (vat_local if vat_local is not None else vat_eur),
        net_eur=net_eur,
        vat_eur=vat_eur,
        invoice_ref=invoice_ref,
    )


@pytest.mark.asyncio
async def test_g2_5_submit_claim_freezes_lines_and_computes_vat_base(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn1 = await _make_txn(
        db_session, org, entity, line_seq=1, net_eur=Decimal("100.00"), vat_eur=Decimal("21.00")
    )
    await _make_txn(
        db_session, org, entity, line_seq=2, net_eur=Decimal("50.00"), vat_eur=Decimal("10.50")
    )
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn1.id)],
        override_minimum=True,  # this test is about the freeze arithmetic, not R8
    )
    await db_session.commit()

    assert result.status == "submitted"
    assert result.vat_eur == Decimal("31.50")  # 21.00 + 10.50
    assert result.vat_local == Decimal("31.50")
    assert result.currency == "EUR"

    fresh_lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert len(fresh_lines) == 1  # single (invoice, product_group) bucket
    assert all(line.frozen_at is not None for line in fresh_lines)


@pytest.mark.asyncio
async def test_g2_5_submit_claim_with_no_lines_freezes_to_zero(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    # No fuel transactions ever ingested; build_claim_lines never called.
    # submit_claim still supplies an invoices list (its own, independent
    # lock primitive) — this scenario exercises freeze with an empty line
    # set (an empty CLAIM SET gate is a future G2.6 concern, not this
    # function's).
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        override_minimum=True,  # zero VAT is trivially below minimum; this test is about the empty-lines case
    )
    assert result.vat_eur == Decimal("0.00")
    assert result.vat_local == Decimal("0.00")
    assert result.currency is None


@pytest.mark.asyncio
async def test_g2_5_freeze_refuses_when_lines_span_multiple_currencies(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    db_session.add_all(
        [
            VatRefundClaimLine(
                org_id=org.id,
                claim_id=claim.id,
                invoice_ref="INV-A",
                product_group="Diesel",
                net_eur=Decimal("10.00"),
                vat_eur=Decimal("2.00"),
                net_local=Decimal("10.00"),
                vat_local=Decimal("2.00"),
                currency="EUR",
            ),
            VatRefundClaimLine(
                org_id=org.id,
                claim_id=claim.id,
                invoice_ref="INV-B",
                product_group="AdBlue",
                net_eur=Decimal("5.00"),
                vat_eur=Decimal("1.00"),
                net_local=Decimal("45.00"),
                vat_local=Decimal("9.00"),
                currency="SEK",
            ),
        ]
    )
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await freeze.freeze_claim_lines(db_session, org.id, claim)
    assert exc.value.code == "claim_currency_mismatch"
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_g2_5_build_claim_lines_refuses_a_bucket_with_mixed_currency_transactions(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    # Same supplier/product/period -> same (UNMATCHED, Diesel) bucket, but
    # different currencies.
    await _make_txn(db_session, org, entity, invoice_ref=None, line_seq=1, currency="EUR")
    await _make_txn(db_session, org, entity, invoice_ref=None, line_seq=2, currency="SEK")
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    assert exc.value.code == "claim_line_mixed_currency"
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_g2_5_lost_lock_race_leaves_the_claim_unfrozen(db_session):
    """Same-session backstop, mirroring WO-51's own test: a lost lock race
    rolls back the freeze too — nothing partially applied."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    from app.models.transport.lock import VatClaimedInvoice

    # Seed a competing lock row directly for the SAME invoice key.
    db_session.add(
        VatClaimedInvoice(
            org_id=org.id,
            claim_id=claim.id,
            entity_id=entity.id,
            refund_country="LV",
            supplier="Q8",
            invoice_ref="INV-0001",
            fuel_transaction_id=txn.id,
        )
    )
    await db_session.commit()

    claim_id, org_id = claim.id, org.id
    with pytest.raises(Exception):  # IntegrityError at flush
        await lock.submit_claim(
            db_session,
            org_id,
            claim_id=claim_id,
            invoices=[("Q8", "INV-0001", txn.id)],
            override_minimum=True,  # must reach the REAL lock race, not fail on R8 first
        )
    await db_session.rollback()

    fresh = await db_session.scalar(
        select(VatRefundClaim).where(VatRefundClaim.id == claim_id, VatRefundClaim.org_id == org_id)
    )
    assert fresh.status == "draft"
    assert fresh.vat_eur is None, "the freeze must not survive the rolled-back submission"

    fresh_lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim_id)
        )
    ).all()
    assert all(line.frozen_at is None for line in fresh_lines), (
        "no line may be left frozen after the whole transition rolled back"
    )


@pytest.mark.asyncio
async def test_g2_5_audit_meta_records_the_frozen_line_count(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        override_minimum=True,  # this test is about the audit meta, not R8
    )
    await db_session.commit()

    event = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id, AuditEvent.action == "transport.claim_submit"
        )
    )
    assert event is not None
    import json

    meta = json.loads(event.meta) if isinstance(event.meta, str) else event.meta
    assert meta["frozen_line_count"] == 1
