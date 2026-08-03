"""G3.2 slice 6 — `statement_ingest.ingest_statement` against Moeve
fixtures. The shared contract is deliberately UNCHANGED by this slice
(WO-65's stability argument: DKV was the last contract-touching network)
— every Moeve fixture line is EUR and rides the existing identity branch;
what is new here is proven at the parser layer (the per-line-IVA 6-dp
reverse calculation, the cash-at-pump provenance flag) and at the gate
layer: the €0.02/€0.03 coversheet tie-out over Moeve's derived figures
through `ingest_statement` itself, PLUS the rule-9 reuse proof — the
harvested ES `(21, 10)` dual entry coheres both Moeve rates with no
parser-side rate table, and an off-table rate WARNS without blocking
(rate policy provably lives in the gate, never duplicated). Mirrors
`test_g3_2_tfc_statement_ingest.py`'s structure.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.supplier_registration import SupplierVatRegistration
from app.services.transport import statement_ingest
from tests.factories.transport import synthetic_moeve_statement
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


def _ecoblue_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "txn_date": "2026-06-10",
        "txn_time": "11:05",
        "vehicle_ref": "Fleet-M-001",
        "station": "Zaragoza Ring Station",
        "country": "ES",
        "product": "ECOBLUE",
        "qty": "100.000",
        "currency": "EUR",
        "gross_local": "100.00",
        "iva_rate": "21",
        "payment": "transfer",
        "invoice_ref": "MOEVE-2026-000321",
    }
    base.update(overrides)
    return base


def _mixed_rate_rows() -> list[dict[str, object]]:
    """The explicit mixed-rate fixture with exactly-known derived figures:
    gasoleo transfer 660.00 @10 -> 600 + 60; EcoBlue transfer 121.00 @21
    -> 100 + 21; gasoleo CASH-AT-PUMP 220.00 @10 -> 200 + 20. The
    VAT-inclusive identity makes the batch total Σ(net+vat) = Σ gross =
    1001.00 exactly — cash lines INCLUDED (the tie-out ties the INVOICE
    total, per the WO-68 cash-at-pump decision)."""
    return [
        _ecoblue_row(
            product="GASOLEO",
            gross_local="660.00",
            iva_rate="10",
            invoice_ref="MOEVE-2026-000322",
        ),
        _ecoblue_row(
            txn_date="2026-06-12",
            vehicle_ref="Fleet-M-002",
            gross_local="121.00",
            invoice_ref="MOEVE-2026-000323",
        ),
        _ecoblue_row(
            txn_date="2026-06-16",
            vehicle_ref="Fleet-M-003",
            station="Valencia Port Station",
            product="GASOLEO",
            gross_local="220.00",
            iva_rate="10",
            payment="cash_at_pump",
            invoice_ref="MOEVE-2026-000324",
        ),
    ]


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_registers_derived_figures(db_session):
    """The headline financial-correctness case: the parser's 6-dp internal
    figures (82.644628 / 17.355372) land q2-rounded at the ingest boundary
    (82.64 / 17.36 / 100.00), on the EUR identity branch, with the
    derivation basis recorded for a human reviewer."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_moeve_statement(rows=[_ecoblue_row()], seed=1).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="moeve.csv",
        content=content,
    )
    await db_session.commit()

    assert result.network == "Moeve"
    assert len(result.lines) == 1
    assert result.entities == []
    assert any("not implemented for this network" in w for w in result.warnings)

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 1
    row = rows[0]

    # 100.00 VAT-inclusive @ 21% -> net 82.644628 (6-dp internal) -> q2 82.64.
    assert row.net_local == Decimal("82.64")
    assert row.vat_local == Decimal("17.36")
    assert row.gross_local == Decimal("100.00")
    assert row.net_eur == Decimal("82.64")
    assert row.vat_eur == Decimal("17.36")
    assert row.fx_source == "eur"
    assert row.fx_rate == Decimal(1)
    assert row.fx_ecb_rate is None
    assert row.provenance_note is not None
    assert "iva_rate=21" in row.provenance_note
    assert "6-dp" in row.provenance_note


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_mixed_rates_and_cash_at_pump_register(db_session):
    """Per-line rates in one statement, each split at its own printed IVA —
    and `net_eur_eff == net_eur` for EVERY row including the cash-at-pump
    one (the PRN discount is ON-INVOICE, inside the printed gross; the
    settlement flag never mutates a figure and creates no off-invoice
    layer)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_moeve_statement(rows=_mixed_rate_rows(), seed=2).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="moeve.csv",
        content=content,
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 3
    by_invoice = {r.invoice_ref: r for r in rows}
    assert by_invoice["MOEVE-2026-000322"].net_eur == Decimal("600.00")
    assert by_invoice["MOEVE-2026-000322"].vat_eur == Decimal("60.00")
    assert by_invoice["MOEVE-2026-000323"].net_eur == Decimal("100.00")
    assert by_invoice["MOEVE-2026-000323"].vat_eur == Decimal("21.00")
    assert by_invoice["MOEVE-2026-000324"].net_eur == Decimal("200.00")
    assert by_invoice["MOEVE-2026-000324"].vat_eur == Decimal("20.00")
    for r in rows:
        assert r.net_eur_eff == r.net_eur
        assert r.fx_source == "eur"

    cash_row = by_invoice["MOEVE-2026-000324"]
    assert "cash at pump" in (cash_row.provenance_note or "")
    # The advisory settlement summary surfaces on the review surface.
    assert any("1 cash-at-pump line(s)" in w and "220.00" in w for w in result.warnings)

    regs = await _registration_rows(db_session, org.id)
    assert regs == [], "MoeveParser never attempts entity detection — no registration is learned"


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_coversheet_within_2_cents_registers(db_session):
    """The live-gate onboarding proof, allowed side: the derived batch
    total is exactly 1001.00 (Σ gross, the VAT-inclusive identity — cash
    lines included); a coversheet of 1001.02 is €0.02 off — within R25's
    tolerance, the statement registers."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_moeve_statement(rows=_mixed_rate_rows(), seed=3).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="moeve.csv",
        content=content,
        coversheet_total=Decimal("1001.02"),
    )
    await db_session.commit()

    assert len(result.lines) == 3
    assert len(await _fuel_rows(db_session, org.id)) == 3


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_coversheet_off_by_3_cents_is_blocked(db_session):
    """The live-gate onboarding proof, blocked side: €0.03 off the derived
    total refuses the whole statement (`capture_review_blocked`) and
    writes zero rows — the WO-66 gate proven over Moeve's per-line-rate
    derived figures, §5.1's "PASS = trained" onboarding contract."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_moeve_statement(rows=_mixed_rate_rows(), seed=4).encode("utf-8")

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="moeve.csv",
            content=content,
            coversheet_total=Decimal("1001.03"),
        )
    assert exc_info.value.code == "capture_review_blocked"
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_harvested_es_rates_raise_no_coherence_warning(
    db_session,
):
    """Assert the ABSENCE: both harvested Moeve rates (10% gasoleo, 21%
    EcoBlue) cohere under the WO-66 gate's harvested ES `(21, 10)` dual
    entry — which exists for exactly this network — so a default fixture
    ingest emits NO `vat_rate_incoherent` finding. The parser carries no
    rate table of its own; this test is the reuse proof."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_moeve_statement(rows=_mixed_rate_rows(), seed=5).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="moeve.csv",
        content=content,
    )
    await db_session.commit()

    assert not any("vat_rate_incoherent" in w or "implied VAT rate" in w for w in result.warnings)
    assert len(await _fuel_rows(db_session, org.id)) == 3


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_off_table_rate_warns_but_registers(db_session):
    """The other direction of the reuse proof: an ES line at 15% (no such
    Spanish rate) parses — the arithmetic is self-consistent — and the
    gate's rule 9 flags it `vat_rate_incoherent` as a WARN that never
    blocks (§2.1a): the statement still registers. Rate policy provably
    lives in the gate, not in a parser-side whitelist."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [_ecoblue_row(gross_local="115.00", iva_rate="15")]
    content = synthetic_moeve_statement(rows=rows, seed=6).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="moeve.csv",
        content=content,
    )
    await db_session.commit()

    assert any("implied VAT rate" in w for w in result.warnings)
    rows_stored = await _fuel_rows(db_session, org.id)
    assert len(rows_stored) == 1
    assert rows_stored[0].net_eur == Decimal("100.00")
    assert rows_stored[0].vat_eur == Decimal("15.00")


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_is_idempotent_on_a_replay(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    content = synthetic_moeve_statement(seed=7).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="m.csv", content=content
    )
    await db_session.commit()

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="m.csv", content=content
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 3, "re-ingesting the identical file must not duplicate transactions"


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_module_disabled_writes_nothing(db_session):
    org = await make_org(db_session)  # transport left OFF (default)
    entity = await make_entity(db_session, org.id)
    content = synthetic_moeve_statement(seed=8).encode("utf-8")

    with pytest.raises(PermissionError):
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="m.csv",
            content=content,
        )

    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_cross_tenant_entity_is_opaque_not_found(db_session):
    org_a = await make_org(db_session)
    org_b = await make_org(db_session)
    await enable_transport(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_moeve_statement(seed=9).encode("utf-8")

    with pytest.raises(NotFoundError):
        await statement_ingest.ingest_statement(
            db_session,
            org_a.id,
            entity_id=entity_b.id,
            period="2026-06",
            filename="m.csv",
            content=content,
        )


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_malformed_row_aborts_the_whole_statement(db_session):
    """An unknown payment channel in the SECOND row must not write the
    FIRST, well-formed row either — the parse failure aborts before phase
    1 even begins (a guessed settlement channel would corrupt the future
    netting picture)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [
        _ecoblue_row(),
        _ecoblue_row(txn_date="2026-06-11", vehicle_ref="Fleet-M-002", payment="voucher"),
    ]
    content = synthetic_moeve_statement(rows=rows, seed=10).encode("utf-8")

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="m.csv",
            content=content,
        )
    assert exc_info.value.code == "unrecognized_fuel_card_statement"
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_moeve_ingest_statement_cross_tenant_isolation_with_overlapping_data(
    db_session,
):
    """Two orgs ingesting the IDENTICAL statement text must never share a
    row — the tenancy-parity overlap discipline (master-context §8)."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_a = await make_entity(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    content = synthetic_moeve_statement(seed=11).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org_a.id,
        entity_id=entity_a.id,
        period="2026-06",
        filename="m.csv",
        content=content,
    )
    await db_session.commit()
    await statement_ingest.ingest_statement(
        db_session,
        org_b.id,
        entity_id=entity_b.id,
        period="2026-06",
        filename="m.csv",
        content=content,
    )
    await db_session.commit()

    rows_a = await _fuel_rows(db_session, org_a.id)
    rows_b = await _fuel_rows(db_session, org_b.id)
    assert len(rows_a) == 3
    assert len(rows_b) == 3
    assert {r.id for r in rows_a}.isdisjoint({r.id for r in rows_b})
