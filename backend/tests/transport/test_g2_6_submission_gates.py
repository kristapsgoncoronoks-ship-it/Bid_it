"""G2.6 slice 1 — `submit_claim` wiring of the period-end gate (R7) and the
Art. 17 minimum-amount gate (R8). Unit coverage for the pure functions lives
in `test_g2_6_deadline.py`/`test_g2_6_minimum.py`; this file proves the
INTEGRATION into `lock.submit_claim`'s gate order (D5).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, fuel_ingest, lock
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _make_claim(db_session, org, entity, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_txn(
    db_session, org, entity, *, net_eur=Decimal("2000.00"), vat_eur=Decimal("420.00")
):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
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
        invoice_ref="INV-0001",
    )


@pytest.mark.asyncio
async def test_g2_6_submit_claim_refuses_before_the_period_has_ended(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)  # 2026-Q2 ends 2026-06-30
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim.id,
            invoices=[("Q8", "INV-0001", txn.id)],
            today=date(2026, 6, 15),  # mid-period — not ended yet
            override_minimum=True,
        )
    assert exc.value.code == "period_not_ended"
    assert exc.value.status == 409

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "draft"


@pytest.mark.asyncio
async def test_g2_6_submit_claim_succeeds_once_the_period_has_ended(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        today=date(2026, 7, 1),  # the day after period end
    )
    assert result.status == "submitted"


@pytest.mark.asyncio
async def test_g2_6_submit_claim_refuses_a_below_minimum_claim(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    # A tiny VAT amount, well below the €400 quarterly threshold.
    txn = await _make_txn(
        db_session, org, entity, net_eur=Decimal("10.00"), vat_eur=Decimal("2.10")
    )
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim.id,
            invoices=[("Q8", "INV-0001", txn.id)],
        )
    assert exc.value.code == "below_minimum"
    assert exc.value.status == 409

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "draft"
    assert fresh.vat_eur is None, "a below-minimum claim must never freeze"

    lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert all(ln.frozen_at is None for ln in lines), "a below-minimum claim must never lock/freeze"


@pytest.mark.asyncio
async def test_g2_6_below_minimum_override_submits_and_records_status_note(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(
        db_session, org, entity, net_eur=Decimal("10.00"), vat_eur=Decimal("2.10")
    )
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        override_minimum=True,
    )
    assert result.status == "submitted"
    assert result.vat_eur == Decimal("2.10")
    assert result.status_note is not None
    assert "Art. 17 minimum override" in result.status_note
    assert "threshold" in result.status_note


@pytest.mark.asyncio
async def test_g2_6_a_national_minimum_country_compares_local_currency_not_eur(db_session):
    """A claim in a NATIONAL_MINIMUMS country (Sweden) whose EUR value would
    clear €400 but whose SEK value is below the fixed 4000 SEK threshold
    must still be blocked — the whole point of R8 comparing the CORRECT
    currency, not a live FX conversion."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="SE", ref_period="2026-Q2"
    )
    await db_session.commit()
    # net_eur/vat_eur EUR-converted might clear 400 EUR, but vat_local (SEK)
    # is well under the 4000 SEK national fixed minimum.
    txn = await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        period="2026-05",
        line_seq=1,
        country="SE",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 15),
        station="Demo Station Stockholm",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="SEK",
        net_local=Decimal("30000.00"),
        vat_local=Decimal("3000.00"),  # below the 4000 SEK quarterly minimum
        gross_local=Decimal("33000.00"),
        net_eur=Decimal("2700.00"),  # would clear the €400 EUR base easily
        vat_eur=Decimal("270.00"),
        invoice_ref="INV-0001",
    )
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=claim.id,
            invoices=[("Q8", "INV-0001", txn.id)],
        )
    assert exc.value.code == "below_minimum"
