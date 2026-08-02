"""G3.3 (WO-66) — the capture review gate wired through the REAL
registration path (`statement_ingest.ingest_statement`): R25's own
acceptance lines proven end to end — "VAT > net on one line ⇒
registration blocked", "batch tie-out off by €0.03 ⇒ blocked; €0.02 ⇒
allowed" — always with ZERO rows written on a block (the two-phase
guarantee extended upstream)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.models.transport.fuel_transaction import FuelTransaction
from app.services.transport import statement_ingest
from tests.factories.transport import (
    synthetic_dkv_statement,
    synthetic_eurowag_statement,
)
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _fuel_rows(db_session, org_id: str):
    return (
        await db_session.scalars(select(FuelTransaction).where(FuelTransaction.org_id == org_id))
    ).all()


def _ew_row(**overrides) -> dict[str, object]:
    """One clean Eurowag CSV row (BE, EUR, 21%) — overridable per test."""
    base: dict[str, object] = {
        "txn_date": "2026-06-01",
        "txn_time": "08:00",
        "vehicle_ref": "V1",
        "station": "S1",
        "country": "BE",
        "product": "DIESEL",
        "qty": "100.000",
        "currency": "EUR",
        "net_local": "100.00",
        "vat_local": "21.00",
        "gross_local": "121.00",
        "invoice_ref": "INV-000001",
    }
    base.update(overrides)
    return base


async def _ingest(db_session, org, entity, rows, **kwargs):
    content = synthetic_eurowag_statement(
        rows=rows, footer_lines=["No seller line here."], seed=1
    ).encode("utf-8")
    return await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="e.csv",
        content=content,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_g3_3_vat_over_net_on_one_line_blocks_registration(db_session):
    """R25 verbatim: VAT > net on ONE line ⇒ registration blocked — and
    the OTHER, clean line is not written either."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    rows = [
        _ew_row(),
        _ew_row(
            txn_date="2026-06-02",
            vehicle_ref="V2",
            net_local="100.00",
            vat_local="123.00",
            gross_local="223.00",
            invoice_ref="INV-000002",
        ),
    ]
    with pytest.raises(ValidationError) as exc_info:
        await _ingest(db_session, org, entity, rows)
    assert exc_info.value.code == "capture_review_blocked"
    assert "vat 123.00 exceeds net 100.00" in str(exc_info.value)
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_3_missing_invoice_number_blocks_registration(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    with pytest.raises(ValidationError) as exc_info:
        await _ingest(db_session, org, entity, [_ew_row(invoice_ref="")])
    assert exc_info.value.code == "capture_review_blocked"
    assert "invoice number is missing" in str(exc_info.value)
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_3_the_refusal_names_every_error_at_once(db_session):
    """Two bad lines ⇒ ONE refusal naming both — the operator sees all
    failures at once (the same discipline as the engine tie-out)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    rows = [
        _ew_row(invoice_ref=""),
        _ew_row(
            txn_date="2026-06-02",
            vehicle_ref="V2",
            net_local="100.00",
            vat_local="123.00",
            gross_local="223.00",
            invoice_ref="INV-000002",
        ),
    ]
    with pytest.raises(ValidationError) as exc_info:
        await _ingest(db_session, org, entity, rows)
    message = str(exc_info.value)
    assert "line 1" in message and "invoice number is missing" in message
    assert "line 2" in message and "vat 123.00 exceeds net 100.00" in message


@pytest.mark.asyncio
async def test_g3_3_coversheet_two_cents_off_registers_three_cents_blocks(db_session):
    """R25 verbatim through the REAL path. The single line totals 121.00
    (net 100 + vat 21): a coversheet of 121.02 registers; 121.03 blocks
    with zero rows written."""
    from decimal import Decimal

    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    with pytest.raises(ValidationError) as exc_info:
        await _ingest(db_session, org, entity, [_ew_row()], coversheet_total=Decimal("121.03"))
    assert exc_info.value.code == "capture_review_blocked"
    assert "batch tie-out failed" in str(exc_info.value)
    assert await _fuel_rows(db_session, org.id) == []

    result = await _ingest(db_session, org, entity, [_ew_row()], coversheet_total=Decimal("121.02"))
    await db_session.commit()
    assert len(result.lines) == 1
    assert len(await _fuel_rows(db_session, org.id)) == 1


@pytest.mark.asyncio
async def test_g3_3_warn_only_statement_registers_with_warnings_surfaced(db_session):
    """Warnings never block (§2.1a verbatim): a PL line at 21% (not a
    known PL rate — 23 or 8) registers, and the coherence warn appears in
    the result's review surface."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    result = await _ingest(
        db_session,
        org,
        entity,
        [_ew_row(country="PL", net_local="100.00", vat_local="21.00", gross_local="121.00")],
    )
    await db_session.commit()

    assert len(result.lines) == 1
    assert any("capture review" in w and "known PL rate" in w for w in result.warnings), (
        result.warnings
    )


@pytest.mark.asyncio
async def test_g3_3_stated_zero_eur_still_hits_the_fx_guard_behind_the_gate(db_session):
    """The moved DKV coverage (see `test_g3_2_dkv_statement_ingest.py`'s
    zero-net test): a stated pair with `net_local > 0` but `net_eur=0`
    PASSES the review gate (the local figures are coherent) and is then
    refused by the FX branch's own zero-guard — proving the gate did not
    swallow `fx_stated_inconsistent`, it only fronts it."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    rows = [
        {
            "txn_date": "2026-06-10",
            "txn_time": "08:00",
            "vehicle_ref": "Fleet-D-001",
            "station": "Stockholm South Hub",
            "country": "SE",
            "product": "DIESEL",
            "qty": "100.000",
            "currency": "SEK",
            "net_local": "1000.00",
            "vat_local": "250.00",
            "gross_local": "1250.00",
            "net_eur": "0.00",  # stated EUR of zero — no derivable implied rate
            "invoice_ref": "DKV-2026-000123",
        }
    ]
    content = synthetic_dkv_statement(rows=rows, seed=2).encode("utf-8")

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="dkv.csv",
            content=content,
        )
    assert exc_info.value.code == "fx_stated_inconsistent"
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_3_default_factory_statements_still_register(db_session):
    """The unchanged-behaviour proof: WO-62's default synthetic statement
    (which always carried an invoice ref and coherent BE 21% figures)
    registers exactly as before the gate existed — no coversheet, no tie
    constraint, no new refusal."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_eurowag_statement(seed=3).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="e.csv",
        content=content,
    )
    await db_session.commit()
    assert len(result.lines) == 1
