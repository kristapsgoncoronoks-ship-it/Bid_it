"""G3.2 slice 4 — `statement_ingest.ingest_statement` against DKV fixtures,
through the ONE deliberate extension this slice makes to it (the
supplier-stated-EUR branch in `_resolve_line`). Proves the headline
correctness property: a stated line's `net_eur` is the SUPPLIER'S figure
verbatim (never an ECB recomputation, even when a cached rate exists), the
VAT is pro-rated at the same implied basis, and — the observable difference
from the ECB branch — a stated line ingests with ZERO cached rates for its
currency. Mirrors `test_g3_2_q8_statement_ingest.py`'s structure.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.supplier_registration import SupplierVatRegistration
from app.services.transport import statement_ingest
from tests.factories.transport import synthetic_dkv_statement
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


def _sek_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
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
        "net_eur": "90.00",
        "invoice_ref": "DKV-2026-000123",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_trusts_stated_eur_and_pro_rates_vat(db_session):
    """The headline financial-correctness case. The suite's conftest seeds
    fallback ECB coverage for SEK (11.2000/EUR), so a cached rate EXISTS
    for the line's date — and is deliberately NOT what converts the line:
    `net_eur` is the supplier's stated 90.00 (an implied 11.111111 SEK/EUR),
    `vat_eur` pro-rates at the same basis, and the cached official rate is
    frozen alongside for audit (R56: both the applied and the official
    rate)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_dkv_statement(rows=[_sek_row()], seed=1).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="dkv.csv",
        content=content,
    )
    await db_session.commit()

    assert result.network == "DKV"
    assert len(result.lines) == 1
    assert result.entities == []
    assert any("not implemented for this network" in w for w in result.warnings)

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 1
    row = rows[0]

    # The supplier's figure, verbatim — 1000 SEK / 11.2 (the cached fallback
    # rate) would have been 89.29, which must NOT appear anywhere.
    assert row.net_eur == Decimal("90.00")
    # Pro-rated at the same stated basis: 250 * 90 / 1000.
    assert row.vat_eur == Decimal("22.50")
    # The implied APPLIED rate, 6 dp: 1000 / 90.
    assert row.fx_rate == Decimal("11.111111")
    assert row.fx_source == "stated"
    # The OFFICIAL reference is frozen alongside for drift audit — the
    # conftest-seeded fallback SEK rate, most recent on-or-before txn_date.
    assert row.fx_ecb_rate == Decimal("11.2000")
    assert row.fx_ecb_date is not None
    assert row.fx_ecb_date <= date(2026, 6, 10)
    # The stated basis is recorded on the line for a human reviewer.
    assert row.provenance_note is not None
    assert "supplier-stated per-line EUR" in row.provenance_note


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_stated_line_ingests_with_no_cached_ecb_rate(db_session):
    """The observable stated-vs-ecb difference: a stated line does NOT
    depend on ECB coverage. ZAR is deliberately outside the conftest's
    seeded fallback currency set — the ECB branch would refuse this line
    outright (`fx_rate_unavailable`, proven by the Q8 suite); the stated
    branch ingests it with NULL reference fields ("no coverage => NULL is
    stored, never a fabricated pass")."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [
        _sek_row(
            country="ZA",
            currency="ZAR",
            net_local="2000.00",
            vat_local="300.00",
            gross_local="2300.00",
            net_eur="100.00",
        )
    ]
    content = synthetic_dkv_statement(rows=rows, seed=2).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="dkv.csv",
        content=content,
    )
    await db_session.commit()

    (row,) = await _fuel_rows(db_session, org.id)
    assert row.net_eur == Decimal("100.00")
    assert row.vat_eur == Decimal("15.00")  # 300 * 100 / 2000
    assert row.fx_rate == Decimal("20.000000")
    assert row.fx_source == "stated"
    assert row.fx_ecb_rate is None
    assert row.fx_ecb_date is None


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_eur_line_rides_the_identity_branch(db_session):
    """The default fixture's DE/EUR line must ingest exactly like every
    other network's EUR lines — identity conversion, untouched by the
    stated branch."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_dkv_statement(seed=3).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="dkv.csv",
        content=content,
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 2
    eur_row = next(r for r in rows if r.currency == "EUR")
    sek_row = next(r for r in rows if r.currency == "SEK")
    assert eur_row.fx_source == "eur"
    assert eur_row.fx_rate == Decimal(1)
    assert eur_row.net_eur == eur_row.net_local
    assert sek_row.fx_source == "stated"


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_inconsistent_stated_line_refuses_and_writes_nothing(
    db_session,
):
    """A stated pair whose implied rate is not strictly positive is refused
    in phase 1 — and the earlier well-formed line is not written either
    (the two-phase guarantee)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [
        _sek_row(),
        _sek_row(
            txn_date="2026-06-11",
            vehicle_ref="Fleet-D-002",
            net_local="1000.00",
            net_eur="-90.00",  # sign-flipped against a positive net_local
        ),
    ]
    content = synthetic_dkv_statement(rows=rows, seed=4).encode("utf-8")

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
    assert await _fuel_rows(db_session, org.id) == [], (
        "the FIRST, well-formed row must not be written when a LATER stated "
        "line in the same statement is inconsistent"
    )


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_zero_net_local_stated_line_is_refused(db_session):
    """`net_local=0` with a nonzero stated EUR figure has no derivable
    implied rate — refused, never guessed."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [_sek_row(net_local="0.00", vat_local="0.00", gross_local="0.00", net_eur="90.00")]
    content = synthetic_dkv_statement(rows=rows, seed=5).encode("utf-8")

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
async def test_g3_2_dkv_ingest_statement_net_eur_eff_defaults_to_stated_net_eur(db_session):
    """`net_eur_eff` stays at `ingest_transaction`'s default (= net_eur, the
    STATED figure) — no off-invoice layer is computed for DKV (its discount
    and service fee are on-invoice, already inside the given figures)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_dkv_statement(seed=6).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="dkv.csv",
        content=content,
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 2
    for r in rows:
        assert r.net_eur_eff == r.net_eur

    regs = await _registration_rows(db_session, org.id)
    assert regs == [], "DKVParser never attempts entity detection — no registration is learned"


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_is_idempotent_on_a_replay(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_dkv_statement(seed=7).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="d.csv", content=content
    )
    await db_session.commit()

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="d.csv", content=content
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 2, "re-ingesting the identical file must not duplicate transactions"


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_module_disabled_writes_nothing(db_session):
    org = await make_org(db_session)  # transport left OFF (default)
    entity = await make_entity(db_session, org.id)
    content = synthetic_dkv_statement(seed=8).encode("utf-8")

    with pytest.raises(PermissionError):
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="d.csv",
            content=content,
        )

    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_cross_tenant_entity_is_opaque_not_found(db_session):
    org_a = await make_org(db_session)
    org_b = await make_org(db_session)
    await enable_transport(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_dkv_statement(seed=9).encode("utf-8")

    with pytest.raises(NotFoundError):
        await statement_ingest.ingest_statement(
            db_session,
            org_a.id,
            entity_id=entity_b.id,
            period="2026-06",
            filename="d.csv",
            content=content,
        )


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_malformed_row_aborts_the_whole_statement(db_session):
    """A malformed row (bad decimal in the SECOND row) must not write the
    FIRST, well-formed row either — the parse failure aborts before phase 1
    even begins."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [
        _sek_row(),
        _sek_row(txn_date="2026-06-11", vehicle_ref="Fleet-D-002", net_local="not-a-number"),
    ]
    content = synthetic_dkv_statement(rows=rows, seed=10).encode("utf-8")

    with pytest.raises(ValidationError):
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="d.csv",
            content=content,
        )

    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_dkv_ingest_statement_cross_tenant_isolation_with_overlapping_data(db_session):
    """Two orgs ingesting the IDENTICAL statement text must never share a
    row — the tenancy-parity overlap discipline (master-context §8)."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_a = await make_entity(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_dkv_statement(seed=11).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org_a.id,
        entity_id=entity_a.id,
        period="2026-06",
        filename="d.csv",
        content=content,
    )
    await db_session.commit()
    await statement_ingest.ingest_statement(
        db_session,
        org_b.id,
        entity_id=entity_b.id,
        period="2026-06",
        filename="d.csv",
        content=content,
    )
    await db_session.commit()

    rows_a = await _fuel_rows(db_session, org_a.id)
    rows_b = await _fuel_rows(db_session, org_b.id)
    assert len(rows_a) == 2
    assert len(rows_b) == 2
    assert {r.id for r in rows_a}.isdisjoint({r.id for r in rows_b})
