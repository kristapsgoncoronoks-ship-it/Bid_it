"""G3.2 slice 3 — `statement_ingest.ingest_statement` against Q8 fixtures,
through the UNMODIFIED function WO-62 shipped. Proves `ingest_statement`'s
network-agnostic design needed zero changes for a THIRD network, and proves
the headline correctness property this slice exists for: a single statement
carrying more than one country/currency ingests every line correctly with
no seller-entity registrations created. Mirrors
`test_g3_2_statement_ingest.py` / `test_g3_2_e100_statement_ingest.py`'s
structure.
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
from app.services.transport import statement_ingest
from tests.factories.transport import synthetic_q8_statement
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _fuel_rows(db_session, org_id: str):
    return (
        await db_session.scalars(
            select(FuelTransaction)
            .where(FuelTransaction.org_id == org_id)
            .order_by(FuelTransaction.line_seq)
        )
    ).all()


async def _registration_rows(db_session, org_id: str):
    return (
        await db_session.scalars(
            select(SupplierVatRegistration).where(SupplierVatRegistration.org_id == org_id)
        )
    ).all()


@pytest.mark.asyncio
async def test_g3_2_q8_ingest_statement_creates_transactions_for_two_countries_and_currencies(
    db_session,
):
    """The headline property: ONE statement, TWO countries, TWO currencies,
    both lines ingest correctly and no entity registration is ever created
    (Q8Parser attempts none)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await fx.load_rates(db_session, [(date(2026, 6, 12), "PLN", Decimal("4.30"))])
    content = synthetic_q8_statement(seed=1).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="q8.csv",
        content=content,
    )
    await db_session.commit()

    assert result.network == "Q8"
    assert len(result.lines) == 2
    assert result.entities == []
    assert any("not implemented for this network" in w for w in result.warnings)

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 2
    countries = {r.country for r in rows}
    currencies = {r.currency for r in rows}
    assert countries == {"EE", "PL"}
    assert currencies == {"EUR", "PLN"}

    eur_row = next(r for r in rows if r.currency == "EUR")
    assert eur_row.fx_source == "eur"
    pln_row = next(r for r in rows if r.currency == "PLN")
    assert pln_row.fx_source == "ecb"
    assert pln_row.fx_rate == Decimal("4.30")

    # net_eur_eff stays at ingest_transaction's default (= net_eur) — the
    # Port One rebate merge is explicitly out of scope for this parser (see
    # `parsers/q8.py`'s module docstring).
    for r in rows:
        assert r.net_eur_eff == r.net_eur

    regs = await _registration_rows(db_session, org.id)
    assert regs == [], "Q8Parser never attempts entity detection — no registration is learned"


@pytest.mark.asyncio
async def test_g3_2_q8_ingest_statement_is_idempotent_on_a_replay(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await fx.load_rates(db_session, [(date(2026, 6, 12), "PLN", Decimal("4.30"))])
    content = synthetic_q8_statement(seed=2).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="q.csv", content=content
    )
    await db_session.commit()

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="q.csv", content=content
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 2, "re-ingesting the identical file must not duplicate transactions"


@pytest.mark.asyncio
async def test_g3_2_q8_ingest_statement_module_disabled_writes_nothing(db_session):
    org = await make_org(db_session)  # transport left OFF (default)
    entity = await make_entity(db_session, org.id)
    content = synthetic_q8_statement(seed=3).encode("utf-8")

    with pytest.raises(PermissionError):
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="q.csv",
            content=content,
        )

    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_q8_ingest_statement_cross_tenant_entity_is_opaque_not_found(db_session):
    org_a = await make_org(db_session)
    org_b = await make_org(db_session)
    await enable_transport(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    # The default fixture's PL/PLN line must resolve its FX rate cleanly in
    # phase 1 (see `statement_ingest`'s "two-phase write" docstring) so the
    # cross-tenant entity check in phase 2 is what actually fires here.
    await fx.load_rates(db_session, [(date(2026, 6, 12), "PLN", Decimal("4.30"))])
    content = synthetic_q8_statement(seed=4).encode("utf-8")

    with pytest.raises(NotFoundError):
        await statement_ingest.ingest_statement(
            db_session,
            org_a.id,
            entity_id=entity_b.id,
            period="2026-06",
            filename="q.csv",
            content=content,
        )


@pytest.mark.asyncio
async def test_g3_2_q8_ingest_statement_unrecognized_file_is_refused_and_writes_nothing(db_session):
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
async def test_g3_2_q8_ingest_statement_invalid_period_is_refused_before_parsing(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="not-a-period",
            filename="q.csv",
            content=b"irrelevant, never reached",
        )
    assert exc_info.value.code == "invalid_period"


@pytest.mark.asyncio
async def test_g3_2_q8_ingest_statement_malformed_row_aborts_the_whole_statement(db_session):
    """A malformed row (bad decimal in the SECOND row) must not write the
    FIRST, well-formed row either — see `statement_ingest`'s "two-phase
    write" docstring."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "EE",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "net_local": "10.00",
            "vat_local": "2.00",
            "gross_local": "12.00",
            "invoice_ref": "",
        },
        {
            "txn_date": "2026-06-02",
            "txn_time": "",
            "vehicle_ref": "V2",
            "station": "S2",
            "country": "PL",
            "product": "DIESEL",
            "qty": "5",
            "currency": "EUR",
            "net_local": "not-a-number",  # malformed
            "vat_local": "2.00",
            "gross_local": "12.00",
            "invoice_ref": "",
        },
    ]
    content = synthetic_q8_statement(rows=rows, seed=5).encode("utf-8")

    with pytest.raises(ValidationError):
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="q.csv",
            content=content,
        )

    assert await _fuel_rows(db_session, org.id) == [], (
        "the FIRST, well-formed row must not be written when a LATER row in "
        "the same statement is malformed"
    )


@pytest.mark.asyncio
async def test_g3_2_q8_ingest_statement_refuses_and_writes_nothing_when_no_fx_rate_is_cached(
    db_session,
):
    """The FIRST line is a clean EUR line; the SECOND needs an uncached
    currency — proves the two-phase design writes NEITHER (master-context
    §4.15: refuse, never guess)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "EE",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "net_local": "10.00",
            "vat_local": "2.00",
            "gross_local": "12.00",
            # WO-66/G3.3: refs added — the capture review gate (rule 1)
            # otherwise refuses before the FX phase this test is about.
            "invoice_ref": "FB/00000801",
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
            "net_local": "100.00",
            "vat_local": "25.00",
            "gross_local": "125.00",
            "invoice_ref": "FB/00000802",
        },
    ]
    content = synthetic_q8_statement(rows=rows, seed=6).encode("utf-8")

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="q.csv",
            content=content,
        )
    assert exc_info.value.code == "fx_rate_unavailable"
    assert await _fuel_rows(db_session, org.id) == [], "the clean EUR line must ALSO not be written"


@pytest.mark.asyncio
async def test_g3_2_q8_ingest_statement_cross_tenant_isolation_with_overlapping_data(db_session):
    """Two orgs ingesting the IDENTICAL statement text must never share a
    row — the tenancy-parity overlap discipline (master-context §8)."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_a = await make_entity(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    await fx.load_rates(db_session, [(date(2026, 6, 12), "PLN", Decimal("4.30"))])
    content = synthetic_q8_statement(seed=7).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org_a.id,
        entity_id=entity_a.id,
        period="2026-06",
        filename="q.csv",
        content=content,
    )
    await db_session.commit()
    await statement_ingest.ingest_statement(
        db_session,
        org_b.id,
        entity_id=entity_b.id,
        period="2026-06",
        filename="q.csv",
        content=content,
    )
    await db_session.commit()

    rows_a = await _fuel_rows(db_session, org_a.id)
    rows_b = await _fuel_rows(db_session, org_b.id)
    assert len(rows_a) == 2
    assert len(rows_b) == 2
    assert {r.id for r in rows_a}.isdisjoint({r.id for r in rows_b})
