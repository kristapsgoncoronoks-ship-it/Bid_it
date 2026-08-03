"""G3.2 slice 5 — `statement_ingest.ingest_statement` against TFC fixtures.
The shared contract is deliberately UNCHANGED by this slice (WO-65's
stability argument: DKV was the last contract-touching network) — every
TFC line is EUR and rides the existing identity branch; what is new here
is proven at the parser layer (the derived-net hub-discount arithmetic)
and at the gate layer: this is the FIRST network onboarded against the
LIVE WO-66 capture-review gate, so the €0.02/€0.03 coversheet tie-out is
proven over TFC's DERIVED figures through `ingest_statement` itself
(`BA_fleet_fuel.md` §5.1's "PASS = trained" onboarding contract). Mirrors
`test_g3_2_dkv_statement_ingest.py`'s structure.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.supplier_registration import SupplierVatRegistration
from app.services.transport import statement_ingest
from tests.factories.transport import synthetic_tfc_statement
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


def _hub_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "txn_date": "2026-06-10",
        "txn_time": "09:15",
        "vehicle_ref": "Fleet-T-001",
        "station": "Antwerp Ring Hub",
        "station_class": "hub",
        "country": "BE",
        "product": "DIESEL",
        "qty": "100.000",
        "currency": "EUR",
        "list_price_eur_l": "1.500",
        "invoice_ref": "TFC-2026-000123",
    }
    base.update(overrides)
    return base


def _three_tier_rows() -> list[dict[str, object]]:
    """The explicit three-tier fixture with exactly-known derived figures:
    hub 100 L x 1.295 -> 129.5 + 27.195; meer 200 L x 1.300 -> 260 + 54.6;
    third_party 150 L x 1.600 -> 240 + 50.4. Unrounded batch total
    (Σ net+vat) = 761.695, q2 -> 761.70."""
    return [
        _hub_row(),
        _hub_row(
            txn_date="2026-06-12",
            vehicle_ref="Fleet-T-002",
            station="Meer Border Hub",
            station_class="meer",
            qty="200.000",
            list_price_eur_l="1.490",
            invoice_ref="TFC-2026-000124",
        ),
        _hub_row(
            txn_date="2026-06-16",
            vehicle_ref="Fleet-T-003",
            station="Utrecht Third-Party Station",
            station_class="third_party",
            country="NL",
            qty="150.000",
            list_price_eur_l="1.600",
            invoice_ref="TFC-2026-000125",
        ),
    ]


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_registers_derived_figures(db_session):
    """The headline financial-correctness case: the parser-derived hub
    figures land q2-rounded (129.50 / 27.20 / 156.70), on the EUR identity
    branch, with the derivation basis recorded for a human reviewer."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_tfc_statement(rows=[_hub_row()], seed=1).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="tfc.csv",
        content=content,
    )
    await db_session.commit()

    assert result.network == "TFC"
    assert len(result.lines) == 1
    assert result.entities == []
    assert any("not implemented for this network" in w for w in result.warnings)

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 1
    row = rows[0]

    # 100 L x (1.500 - 0.205) = 129.5 net; flat 21% -> 27.195 -> q2 27.20.
    assert row.net_local == Decimal("129.50")
    assert row.vat_local == Decimal("27.20")
    assert row.gross_local == Decimal("156.70")
    assert row.net_eur == Decimal("129.50")
    assert row.vat_eur == Decimal("27.20")
    assert row.fx_source == "eur"
    assert row.fx_rate == Decimal(1)
    assert row.fx_ecb_rate is None
    assert row.provenance_note is not None
    assert "0.205" in row.provenance_note
    assert "21" in row.provenance_note


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_all_three_tiers_register(db_session):
    """All three discount tiers in one statement, each priced by its own
    per-litre constant — and `net_eur_eff == net_eur` for every row (the
    hub discount is ON-INVOICE, already inside the derived net; no
    off-invoice layer exists for TFC)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_tfc_statement(rows=_three_tier_rows(), seed=2).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="tfc.csv",
        content=content,
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 3
    by_station = {r.station: r for r in rows}
    assert by_station["Antwerp Ring Hub"].net_eur == Decimal("129.50")
    assert by_station["Antwerp Ring Hub"].vat_eur == Decimal("27.20")
    assert by_station["Meer Border Hub"].net_eur == Decimal("260.00")
    assert by_station["Meer Border Hub"].vat_eur == Decimal("54.60")
    assert by_station["Utrecht Third-Party Station"].net_eur == Decimal("240.00")
    assert by_station["Utrecht Third-Party Station"].vat_eur == Decimal("50.40")
    for r in rows:
        assert r.net_eur_eff == r.net_eur
        assert r.fx_source == "eur"

    regs = await _registration_rows(db_session, org.id)
    assert regs == [], "TFCParser never attempts entity detection — no registration is learned"


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_coversheet_within_2_cents_registers(db_session):
    """The live-gate onboarding proof, allowed side: the derived batch
    total is 761.695 -> q2 761.70; a coversheet of 761.72 is €0.02 off —
    within R25's tolerance, the statement registers."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_tfc_statement(rows=_three_tier_rows(), seed=3).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="tfc.csv",
        content=content,
        coversheet_total=Decimal("761.72"),
    )
    await db_session.commit()

    assert len(result.lines) == 3
    assert len(await _fuel_rows(db_session, org.id)) == 3


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_coversheet_off_by_3_cents_is_blocked(db_session):
    """The live-gate onboarding proof, blocked side: €0.03 off the DERIVED
    total refuses the whole statement (`capture_review_blocked`) and
    writes zero rows — the WO-66 gate proven over a NEW network's
    parser-computed figures, §5.1's "PASS = trained" run for real."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_tfc_statement(rows=_three_tier_rows(), seed=4).encode("utf-8")

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="tfc.csv",
            content=content,
            coversheet_total=Decimal("761.73"),
        )
    assert exc_info.value.code == "capture_review_blocked"
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_flat_21_pct_raises_no_coherence_warning(db_session):
    """Assert the ABSENCE: the parser derives VAT at exactly 21%, which is
    the BE/NL standard rate — the WO-66 gate's rule-9 coherence check must
    emit NO `vat_rate_incoherent` finding over a default TFC fixture."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_tfc_statement(seed=5).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="tfc.csv",
        content=content,
    )
    await db_session.commit()

    assert not any("vat_rate_incoherent" in w or "implied VAT rate" in w for w in result.warnings)
    assert len(await _fuel_rows(db_session, org.id)) == 3


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_is_idempotent_on_a_replay(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_tfc_statement(seed=6).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="t.csv", content=content
    )
    await db_session.commit()

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="t.csv", content=content
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 3, "re-ingesting the identical file must not duplicate transactions"


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_module_disabled_writes_nothing(db_session):
    org = await make_org(db_session)  # transport left OFF (default)
    entity = await make_entity(db_session, org.id)
    content = synthetic_tfc_statement(seed=7).encode("utf-8")

    with pytest.raises(PermissionError):
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="t.csv",
            content=content,
        )

    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_cross_tenant_entity_is_opaque_not_found(db_session):
    org_a = await make_org(db_session)
    org_b = await make_org(db_session)
    await enable_transport(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_tfc_statement(seed=8).encode("utf-8")

    with pytest.raises(NotFoundError):
        await statement_ingest.ingest_statement(
            db_session,
            org_a.id,
            entity_id=entity_b.id,
            period="2026-06",
            filename="t.csv",
            content=content,
        )


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_malformed_row_aborts_the_whole_statement(db_session):
    """An unknown station_class in the SECOND row must not write the FIRST,
    well-formed row either — the parse failure aborts before phase 1 even
    begins (a guessed tier is an invented discount)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [
        _hub_row(),
        _hub_row(txn_date="2026-06-11", vehicle_ref="Fleet-T-002", station_class="franchise"),
    ]
    content = synthetic_tfc_statement(rows=rows, seed=9).encode("utf-8")

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="t.csv",
            content=content,
        )
    assert exc_info.value.code == "unrecognized_fuel_card_statement"
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_tfc_ingest_statement_cross_tenant_isolation_with_overlapping_data(db_session):
    """Two orgs ingesting the IDENTICAL statement text must never share a
    row — the tenancy-parity overlap discipline (master-context §8)."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_a = await make_entity(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_tfc_statement(seed=10).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org_a.id,
        entity_id=entity_a.id,
        period="2026-06",
        filename="t.csv",
        content=content,
    )
    await db_session.commit()
    await statement_ingest.ingest_statement(
        db_session,
        org_b.id,
        entity_id=entity_b.id,
        period="2026-06",
        filename="t.csv",
        content=content,
    )
    await db_session.commit()

    rows_a = await _fuel_rows(db_session, org_a.id)
    rows_b = await _fuel_rows(db_session, org_b.id)
    assert len(rows_a) == 3
    assert len(rows_b) == 3
    assert {r.id for r in rows_a}.isdisjoint({r.id for r in rows_b})
