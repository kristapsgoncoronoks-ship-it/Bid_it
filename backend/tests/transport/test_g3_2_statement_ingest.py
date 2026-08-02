"""G3.2 slice 1 — `statement_ingest.ingest_statement`, the real caller of
`fuel_ingest.ingest_transaction` (G1.2/WO-50) and `supplier_entity.
learn_registration` (G3.1/WO-61) both of which shipped with "no real caller
yet". See `test_g3_2_fuel_card_parser.py` for the pure parser/registry
tests.
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
from tests.factories.transport import synthetic_eurowag_statement, synthetic_vat_id
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
async def test_g3_2_ingest_statement_creates_transactions_and_learns_the_entity(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_eurowag_statement(seed=1).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="eurowag.csv",
        content=content,
    )
    await db_session.commit()

    assert result.network == "Eurowag"
    assert len(result.lines) == 1
    assert len(result.entities) == 1
    assert result.entities[0].source == "capture"
    assert result.entities[0].country == "BE"

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 1
    assert rows[0].supplier == "Eurowag"
    assert rows[0].period == "2026-06"
    assert rows[0].country == "BE"
    assert rows[0].product_group == "Diesel"  # derived from "DIESEL"

    regs = await _registration_rows(db_session, org.id)
    assert len(regs) == 1
    assert regs[0].supplier == "Eurowag"
    assert regs[0].country == "BE"
    assert regs[0].source == "capture"


@pytest.mark.asyncio
async def test_g3_2_ingest_statement_is_idempotent_on_a_replay(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_eurowag_statement(seed=2).encode("utf-8")

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
async def test_g3_2_ingest_statement_never_overwrites_a_curated_registration(db_session):
    """R22 end-to-end, through the REAL caller this order wires up."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    curated_vat = synthetic_vat_id("BE", seed=99)
    await supplier_entity.set_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=curated_vat,
        entity_name="Eurowag Belgium BVBA (curated by admin)",
    )
    await db_session.commit()

    content = synthetic_eurowag_statement(seed=3).encode("utf-8")
    result = await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="e.csv", content=content
    )
    await db_session.commit()

    assert result.entities[0].source == "admin"
    assert result.entities[0].vat_number == curated_vat

    regs = await _registration_rows(db_session, org.id)
    assert len(regs) == 1
    assert regs[0].entity_name == "Eurowag Belgium BVBA (curated by admin)"


@pytest.mark.asyncio
async def test_g3_2_ingest_statement_module_disabled_writes_nothing(db_session):
    org = await make_org(db_session)  # transport left OFF (default)
    entity = await make_entity(db_session, org.id)
    content = synthetic_eurowag_statement(seed=4).encode("utf-8")

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
async def test_g3_2_ingest_statement_cross_tenant_entity_is_opaque_not_found(db_session):
    org_a = await make_org(db_session)
    org_b = await make_org(db_session)
    await enable_transport(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_eurowag_statement(seed=5).encode("utf-8")

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
async def test_g3_2_ingest_statement_unrecognized_file_is_refused_and_writes_nothing(db_session):
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
async def test_g3_2_ingest_statement_invalid_period_is_refused_before_parsing(db_session):
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
async def test_g3_2_ingest_statement_malformed_row_aborts_the_whole_statement(db_session):
    """A malformed row must not write the OTHER, good rows either — see
    `statement_ingest`'s "two-phase write" docstring."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "BE",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "net_local": "10.00",
            "vat_local": "2.10",
            "gross_local": "12.10",
            "invoice_ref": "",
        },
        {
            "txn_date": "2026-06-02",
            "txn_time": "",
            "vehicle_ref": "V2",
            "station": "S2",
            "country": "BE",
            "product": "AdBlue",
            "qty": "not-a-number",  # malformed
            "currency": "EUR",
            "net_local": "5.00",
            "vat_local": "1.05",
            "gross_local": "6.05",
            "invoice_ref": "",
        },
    ]
    content = synthetic_eurowag_statement(rows=rows, seed=6).encode("utf-8")

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
async def test_g3_2_ingest_statement_converts_a_non_eur_line_via_the_cached_ecb_rate(db_session):
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
            "net_local": "430.00",
            "vat_local": "98.90",
            "gross_local": "528.90",
            # WO-66/G3.3: the capture review gate now refuses a line with no
            # invoice reference (rule 1, error) — this fixture's subject is
            # the ECB conversion, so it carries a synthetic ref.
            "invoice_ref": "INV-000701",
        }
    ]
    content = synthetic_eurowag_statement(rows=rows, footer_lines=["No seller line here."], seed=7)

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
    assert str(txn.net_eur) == "100.00"  # 430.00 / 4.30
    assert txn.fx_source == "ecb"
    assert txn.fx_rate == Decimal("4.30")
    assert txn.fx_ecb_date == date(2026, 6, 10)


@pytest.mark.asyncio
async def test_g3_2_ingest_statement_refuses_and_writes_nothing_when_no_fx_rate_is_cached(
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
            "country": "BE",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "net_local": "10.00",
            "vat_local": "2.10",
            "gross_local": "12.10",
            # WO-66/G3.3: refs added — the capture review gate (rule 1)
            # otherwise refuses before the FX phase this test is about.
            "invoice_ref": "INV-000801",
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
            # fallback currency set (`app.services.fx.FALLBACK_RATES`) — the
            # ONE currency in this suite guaranteed to have zero cached rate.
            "currency": "ZAR",
            "net_local": "100.00",
            "vat_local": "25.00",
            "gross_local": "125.00",
            "invoice_ref": "INV-000802",
        },
    ]
    content = synthetic_eurowag_statement(rows=rows, seed=8)

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
async def test_g3_2_ingest_statement_warns_but_still_ingests_when_no_entity_is_detected(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_eurowag_statement(footer_lines=["Thank you for your business."], seed=9)

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
async def test_g3_2_ingest_statement_cross_tenant_isolation_with_overlapping_data(db_session):
    """Two orgs ingesting the IDENTICAL statement text must never share a row
    — the tenancy-parity overlap discipline (master-context §8)."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_a = await make_entity(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_eurowag_statement(seed=10).encode("utf-8")

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
