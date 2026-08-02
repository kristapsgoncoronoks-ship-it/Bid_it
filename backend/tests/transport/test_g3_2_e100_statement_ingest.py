"""G3.2 slice 2 — `statement_ingest.ingest_statement` against E100
fixtures, through the UNMODIFIED function WO-62 shipped. Proves
`ingest_statement`'s network-agnostic design needed zero changes for a
second, structurally different (VAT-inclusive-gross) network. Mirrors
`test_g3_2_statement_ingest.py`'s structure exactly for the Eurowag case.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.supplier_registration import SupplierVatRegistration
from app.services import fx
from app.services.transport import statement_ingest, supplier_entity
from tests.factories.transport import synthetic_e100_statement, synthetic_vat_id
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _fuel_rows(db_session, org_id: str):
    return (
        await db_session.scalars(select(FuelTransaction).where(FuelTransaction.org_id == org_id))
    ).all()


async def _registration_rows(db_session, org_id: str):
    return (
        await db_session.scalars(
            select(SupplierVatRegistration).where(SupplierVatRegistration.org_id == org_id)
        )
    ).all()


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_creates_transactions_and_learns_the_entity(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_e100_statement(seed=1).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="e100.csv",
        content=content,
    )
    await db_session.commit()

    assert result.network == "E100"
    assert len(result.lines) == 1
    assert len(result.entities) == 1
    assert result.entities[0].source == "capture"
    assert result.entities[0].country == "PL"

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 1
    txn = rows[0]
    assert txn.supplier == "E100"
    assert txn.period == "2026-06"
    assert txn.country == "PL"
    assert txn.product_group == "Diesel"  # derived from "DIESEL"

    # net_eur/vat_eur reflect the reverse-calculated, q2-rounded figures —
    # never the raw VAT-inclusive gross.
    gross = txn.gross_local
    assert txn.net_eur != gross
    assert (txn.net_eur + txn.vat_eur - gross).copy_abs() <= Decimal("0.01")

    regs = await _registration_rows(db_session, org.id)
    assert len(regs) == 1
    assert regs[0].supplier == "E100"
    assert regs[0].country == "PL"
    assert regs[0].source == "capture"


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_is_idempotent_on_a_replay(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_e100_statement(seed=2).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="e.csv", content=content
    )
    await db_session.commit()

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="e.csv", content=content
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 1, "re-ingesting the identical file must not duplicate transactions"

    regs = await _registration_rows(db_session, org.id)
    assert len(regs) == 1, "re-ingesting must not duplicate a learned registration"


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_never_overwrites_a_curated_registration(db_session):
    """R22 end-to-end for a SECOND network."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    curated_vat = synthetic_vat_id("PL", seed=99)
    await supplier_entity.set_registration(
        db_session,
        org.id,
        supplier="E100",
        country="PL",
        vat_number=curated_vat,
        entity_name="E100 International Trade sp. z o.o. (curated by admin)",
    )
    await db_session.commit()

    content = synthetic_e100_statement(seed=3).encode("utf-8")
    result = await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="e.csv", content=content
    )
    await db_session.commit()

    assert result.entities[0].source == "admin"
    assert result.entities[0].vat_number == curated_vat

    regs = await _registration_rows(db_session, org.id)
    assert len(regs) == 1
    assert regs[0].entity_name == "E100 International Trade sp. z o.o. (curated by admin)"


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_module_disabled_writes_nothing(db_session):
    org = await make_org(db_session)  # transport left OFF (default)
    entity = await make_entity(db_session, org.id)
    content = synthetic_e100_statement(seed=4).encode("utf-8")

    with pytest.raises(PermissionError):
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="e.csv",
            content=content,
        )

    assert await _fuel_rows(db_session, org.id) == []
    assert await _registration_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_cross_tenant_entity_is_opaque_not_found(db_session):
    org_a = await make_org(db_session)
    org_b = await make_org(db_session)
    await enable_transport(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_e100_statement(seed=5).encode("utf-8")

    with pytest.raises(NotFoundError):
        await statement_ingest.ingest_statement(
            db_session,
            org_a.id,
            entity_id=entity_b.id,
            period="2026-06",
            filename="e.csv",
            content=content,
        )


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_unrecognized_file_is_refused_and_writes_nothing(
    db_session,
):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="mystery.csv",
            content=b"nothing recognisable here",
        )
    assert exc_info.value.code == "unrecognized_fuel_card_statement"
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_invalid_period_is_refused_before_parsing(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="not-a-period",
            filename="e.csv",
            content=b"irrelevant, never reached",
        )
    assert exc_info.value.code == "invalid_period"


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_malformed_row_aborts_the_whole_statement(db_session):
    """A malformed row (out-of-range vat_rate) must not write the OTHER,
    good row either — see `statement_ingest`'s "two-phase write" docstring."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "PL",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "gross_local": "12.10",
            "vat_rate": "21",
            "invoice_ref": "",
        },
        {
            "txn_date": "2026-06-02",
            "txn_time": "",
            "vehicle_ref": "V2",
            "station": "S2",
            "country": "PL",
            "product": "AdBlue",
            "qty": "5",
            "currency": "EUR",
            "gross_local": "6.05",
            "vat_rate": "150",  # malformed — out of range
            "invoice_ref": "",
        },
    ]
    content = synthetic_e100_statement(rows=rows, seed=6).encode("utf-8")

    with pytest.raises(ValidationError):
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="e.csv",
            content=content,
        )

    assert await _fuel_rows(db_session, org.id) == [], (
        "the FIRST, well-formed row must not be written when a LATER row in "
        "the same statement is malformed"
    )


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_converts_a_non_eur_line_via_the_cached_ecb_rate(
    db_session,
):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await fx.load_rates(db_session, [(date(2026, 6, 10), "PLN", Decimal("4.30"))])

    rows = [
        {
            "txn_date": "2026-06-10",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "Warsaw Hub",
            "country": "PL",
            "product": "DIESEL",
            "qty": "100",
            "currency": "PLN",
            "gross_local": "528.90",
            "vat_rate": "21",
            # WO-66/G3.3: the capture review gate now refuses a line with no
            # invoice reference (rule 1, error) — this fixture's subject is
            # the ECB conversion, so it carries a synthetic ref.
            "invoice_ref": "E1-2026-000701",
        }
    ]
    content = synthetic_e100_statement(rows=rows, footer_lines=["No seller marker here."], seed=7)

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="e.csv",
        content=content.encode("utf-8"),
    )
    await db_session.commit()

    txn = result.lines[0]
    assert txn.currency == "PLN"
    assert txn.fx_source == "ecb"
    assert txn.fx_rate == Decimal("4.30")
    assert txn.fx_ecb_date == date(2026, 6, 10)
    # 528.90 / 1.21 = 437.10 net_local; 437.10 / 4.30 = 101.6511... -> q2 101.65
    assert str(txn.net_eur) == "101.65"


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_refuses_and_writes_nothing_when_no_fx_rate_is_cached(
    db_session,
):
    """The FIRST line is a clean EUR line; the SECOND needs an uncached
    currency — proves the two-phase design writes NEITHER, not just the bad
    one (master-context §4.15: refuse, never guess)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "PL",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "gross_local": "12.10",
            "vat_rate": "21",
            # WO-66/G3.3: refs added — the capture review gate (rule 1)
            # otherwise refuses before the FX phase this test is about.
            "invoice_ref": "E1-2026-000801",
        },
        {
            "txn_date": "2026-06-02",
            "txn_time": "",
            "vehicle_ref": "V2",
            "station": "S2",
            "country": "ZA",
            "product": "DIESEL",
            "qty": "10",
            # ZAR is deliberately outside the test fixture's bundled ECB/
            # fallback currency set — the ONE currency in this suite
            # guaranteed to have zero cached rate.
            "currency": "ZAR",
            "gross_local": "125.00",
            "vat_rate": "25",
            "invoice_ref": "E1-2026-000802",
        },
    ]
    content = synthetic_e100_statement(rows=rows, seed=8)

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="e.csv",
            content=content.encode("utf-8"),
        )
    assert exc_info.value.code == "fx_rate_unavailable"
    assert await _fuel_rows(db_session, org.id) == [], "the clean EUR line must ALSO not be written"


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_warns_but_still_ingests_when_no_entity_is_detected(
    db_session,
):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_e100_statement(footer_lines=["Thank you for your business."], seed=9)

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="e.csv",
        content=content.encode("utf-8"),
    )
    await db_session.commit()

    assert len(result.lines) == 1
    assert result.entities == []
    assert any("no per-country seller entity detected" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_g3_2_e100_ingest_statement_cross_tenant_isolation_with_overlapping_data(db_session):
    """Two orgs ingesting the IDENTICAL statement text must never share a
    row — the tenancy-parity overlap discipline (master-context §8)."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_a = await make_entity(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_e100_statement(seed=10).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org_a.id,
        entity_id=entity_a.id,
        period="2026-06",
        filename="e.csv",
        content=content,
    )
    await db_session.commit()
    await statement_ingest.ingest_statement(
        db_session,
        org_b.id,
        entity_id=entity_b.id,
        period="2026-06",
        filename="e.csv",
        content=content,
    )
    await db_session.commit()

    rows_a = await _fuel_rows(db_session, org_a.id)
    rows_b = await _fuel_rows(db_session, org_b.id)
    assert len(rows_a) == 1
    assert len(rows_b) == 1
    assert rows_a[0].id != rows_b[0].id

    regs_a = await _registration_rows(db_session, org_a.id)
    regs_b = await _registration_rows(db_session, org_b.id)
    assert len(regs_a) == 1
    assert len(regs_b) == 1
    assert regs_a[0].id != regs_b[0].id
