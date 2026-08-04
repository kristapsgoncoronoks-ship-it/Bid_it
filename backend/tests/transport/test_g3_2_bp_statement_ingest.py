"""G3.2 slice 7 — `statement_ingest.ingest_statement` against BP/Aral
fixtures. The shared contract is deliberately UNCHANGED by this slice
(WO-65's stability argument: DKV was the last contract-touching network) —
what is new here is that the EXISTING dated-ECB-rate branch becomes a
network's DEFAULT conversion path for the first time (BP is all-PLN; Q8
exercised the branch with one line), the €0.02/€0.03 coversheet tie-out
runs over DOCUMENT-currency (PLN) totals, rule 9's harvested PL `(23, 8)`
dual entry coheres the fuel/toll/ORS line mix with no parser-side rate
table, and the two-phase guarantee is proven over an uncached currency
(refuse, never guess — the harvested `month_config.FX` fallback is
deliberately NOT reproduced; master-context §4.15 wins). Mirrors
`test_g3_2_moeve_statement_ingest.py`'s structure.
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
from tests.factories.transport import synthetic_bp_statement
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


async def _load_pln_rate(db_session) -> None:
    """The dated ECB reference every explicit fixture row converts at —
    4.30 PLN per 1 EUR on the fixture date (ECB convention: divide)."""
    await fx.load_rates(db_session, [(date(2026, 6, 4), "PLN", Decimal("4.30"))])


def _fuel_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "txn_date": "2026-06-04",
        "txn_time": "10:20",
        "vehicle_ref": "Fleet-P-001",
        "station": "Poznan A2 Services",
        "country": "PL",
        "product": "DIESEL",
        "qty": "200.000",
        "currency": "PLN",
        "net_local": "1000.00",
        "vat_local": "230.00",
        "gross_local": "1230.00",
        "line_type": "fuel",
        "invoice_ref": "BP-2026-000451",
    }
    base.update(overrides)
    return base


def _exact_rows() -> list[dict[str, object]]:
    """The explicit all-PLN fixture with exactly-known figures: fuel
    1000.00+230.00 @23, A2 toll 200.00+16.00 @8 (the harvested PL reduced
    rate), ORS fee 5.00+1.15 @23 (the fee = 2.5% of the toll net — the
    harvested ratio shape). Σ(net+vat) = 1452.15 PLN exactly — the
    DOCUMENT-currency invoice total the tie-out ties, fee lines included
    (THE ORS-FEE-LINE DECISION)."""
    return [
        _fuel_row(),
        _fuel_row(
            product="A2 TOLL",
            qty="1",
            net_local="200.00",
            vat_local="16.00",
            gross_local="216.00",
            line_type="toll",
            invoice_ref="BP-2026-000452",
        ),
        _fuel_row(
            product="ORS FEE",
            qty="1",
            net_local="5.00",
            vat_local="1.15",
            gross_local="6.15",
            line_type="ors_fee",
            invoice_ref="BP-2026-000453",
        ),
    ]


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_registers_pln_figures_via_the_ecb_branch(db_session):
    """The headline financial-correctness case: an all-PLN statement rides
    the EXISTING dated-ECB-rate branch as its DEFAULT path — both amounts
    divided by the same 4.30 rate at each line's own date, the official
    reference frozen, q2 at the boundary — and the ORS decision's
    goods-code path is observable in `product_group` (Diesel / Toll/Fees /
    Service/Other)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _load_pln_rate(db_session)
    content = synthetic_bp_statement(rows=_exact_rows(), seed=1).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="bp.csv",
        content=content,
    )
    await db_session.commit()

    assert result.network == "BP"
    assert len(result.lines) == 3
    assert result.entities == []
    assert any("not implemented for this network" in w for w in result.warnings)
    # The two documented-decision advisories surface on the review surface.
    assert any("MPP" in w and "settlement-side only" in w for w in result.warnings)
    assert any("1 ORS fee line(s)" in w for w in result.warnings)

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 3
    by_invoice = {r.invoice_ref: r for r in rows}

    fuel = by_invoice["BP-2026-000451"]
    # 1000.00 / 4.30 = 232.5581... -> 232.56; 230.00 / 4.30 = 53.4883... -> 53.49.
    assert fuel.net_eur == Decimal("232.56")
    assert fuel.vat_eur == Decimal("53.49")
    assert fuel.product_group == "Diesel"

    toll = by_invoice["BP-2026-000452"]
    # 200.00 / 4.30 = 46.5116... -> 46.51; 16.00 / 4.30 = 3.7209... -> 3.72.
    assert toll.net_eur == Decimal("46.51")
    assert toll.vat_eur == Decimal("3.72")
    assert toll.product_group == "Toll/Fees"

    ors = by_invoice["BP-2026-000453"]
    # 5.00 / 4.30 = 1.1627... -> 1.16; 1.15 / 4.30 = 0.2674... -> 0.27.
    assert ors.net_eur == Decimal("1.16")
    assert ors.vat_eur == Decimal("0.27")
    assert ors.product_group == "Service/Other"

    for r in rows:
        assert r.currency == "PLN"
        assert r.fx_source == "ecb"
        assert r.fx_rate == Decimal("4.30")
        assert r.fx_ecb_rate == Decimal("4.30")
        assert r.fx_ecb_date == date(2026, 6, 4)
        assert r.net_eur_eff == r.net_eur
        assert r.provenance_note is not None and r.provenance_note.startswith("line_type=")

    regs = await _registration_rows(db_session, org.id)
    assert regs == [], "BPParser never attempts entity detection — no registration is learned"


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_coversheet_within_2_groszy_registers(db_session):
    """The live-gate onboarding proof, allowed side: the batch total is
    exactly 1452.15 PLN in DOCUMENT currency (Σ net+vat, fee lines
    included); a coversheet of 1452.17 is 0.02 off — within R25's
    tolerance, the statement registers."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _load_pln_rate(db_session)
    content = synthetic_bp_statement(rows=_exact_rows(), seed=2).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="bp.csv",
        content=content,
        coversheet_total=Decimal("1452.17"),
    )
    await db_session.commit()

    assert len(result.lines) == 3
    assert len(await _fuel_rows(db_session, org.id)) == 3


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_coversheet_off_by_3_groszy_is_blocked(db_session):
    """The live-gate onboarding proof, blocked side: 0.03 off the 1452.15
    PLN document total refuses the whole statement
    (`capture_review_blocked`) and writes zero rows — the WO-66 gate
    proven over the seventh network's figures, §5.1's "PASS = trained"
    onboarding contract, G3.2's last onboarding."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _load_pln_rate(db_session)
    content = synthetic_bp_statement(rows=_exact_rows(), seed=3).encode("utf-8")

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="bp.csv",
            content=content,
            coversheet_total=Decimal("1452.18"),
        )
    assert exc_info.value.code == "capture_review_blocked"
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_harvested_pl_rates_raise_no_coherence_warning(db_session):
    """Assert the ABSENCE: 23% fuel, 8% A2 toll and 23% ORS fee all cohere
    under the WO-66 gate's harvested PL `(23, 8)` dual entry — which
    exists for exactly this network's line mix — so the ingest emits NO
    `vat_rate_incoherent` finding. The parser carries no rate table of
    its own; this test is the reuse proof (the same argument WO-68
    documented for Moeve's ES rates)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _load_pln_rate(db_session)
    content = synthetic_bp_statement(rows=_exact_rows(), seed=4).encode("utf-8")

    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="bp.csv",
        content=content,
    )
    await db_session.commit()

    assert not any("vat_rate_incoherent" in w or "implied VAT rate" in w for w in result.warnings)
    assert len(await _fuel_rows(db_session, org.id)) == 3


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_refuses_and_writes_nothing_when_no_fx_rate_is_cached(
    db_session,
):
    """The uncached-currency two-phase-guarantee test: the FIRST line is a
    clean EUR line; the SECOND needs a currency with ZERO rate coverage —
    the statement refuses (`fx_rate_unavailable`) and writes NEITHER
    line. This is the documented refusal of the harvested
    `month_config.FX` fallback: master-context §4.15 (refuse, never
    guess) wins over the retired system's static per-month table. PLN
    itself cannot be the uncached probe here — the platform's rate store
    ships a bundled, DATED indicative PLN snapshot (`fx.FALLBACK_RATES`,
    an honest recorded rate, not a silent constant — the same reason
    Q8's suite probes with ZAR), so the zero-coverage currency is ZAR
    while the fixture keeps BP's statement shape."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    rows = [
        _fuel_row(
            country="DE",
            station="Frankfurt Depot",
            currency="EUR",
            net_local="100.00",
            vat_local="19.00",
            gross_local="119.00",
            invoice_ref="BP-2026-000460",
        ),
        _fuel_row(
            txn_date="2026-06-05",
            vehicle_ref="Fleet-P-002",
            country="ZA",
            station="Out-of-network Stop",
            # ZAR is deliberately outside the bundled ECB/fallback currency
            # set — the ONE currency in this suite guaranteed to have zero
            # cached rate.
            currency="ZAR",
            invoice_ref="BP-2026-000461",
        ),
    ]
    content = synthetic_bp_statement(rows=rows, seed=5).encode("utf-8")

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="bp.csv",
            content=content,
        )
    assert exc_info.value.code == "fx_rate_unavailable"
    assert await _fuel_rows(db_session, org.id) == [], "the clean EUR line must ALSO not be written"


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_is_idempotent_on_a_replay(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _load_pln_rate(db_session)
    content = synthetic_bp_statement(seed=6).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="b.csv", content=content
    )
    await db_session.commit()

    await statement_ingest.ingest_statement(
        db_session, org.id, entity_id=entity.id, period="2026-06", filename="b.csv", content=content
    )
    await db_session.commit()

    rows = await _fuel_rows(db_session, org.id)
    assert len(rows) == 3, "re-ingesting the identical file must not duplicate transactions"


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_module_disabled_writes_nothing(db_session):
    org = await make_org(db_session)  # transport left OFF (default)
    entity = await make_entity(db_session, org.id)
    content = synthetic_bp_statement(seed=7).encode("utf-8")

    with pytest.raises(PermissionError):
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="b.csv",
            content=content,
        )

    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_cross_tenant_entity_is_opaque_not_found(db_session):
    org_a = await make_org(db_session)
    org_b = await make_org(db_session)
    await enable_transport(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    # The default fixture's PLN lines must resolve their FX rate cleanly in
    # phase 1 (see `statement_ingest`'s "two-phase write" docstring) so the
    # cross-tenant entity check in phase 2 is what actually fires here.
    await _load_pln_rate(db_session)
    content = synthetic_bp_statement(seed=8).encode("utf-8")

    with pytest.raises(NotFoundError):
        await statement_ingest.ingest_statement(
            db_session,
            org_a.id,
            entity_id=entity_b.id,
            period="2026-06",
            filename="b.csv",
            content=content,
        )


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_malformed_row_aborts_the_whole_statement(db_session):
    """An unknown line_type in the SECOND row must not write the FIRST,
    well-formed row either — the parse failure aborts before phase 1 even
    begins (a guessed line kind would mis-map the Art. 9 goods-code
    path)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    rows = [
        _fuel_row(),
        _fuel_row(txn_date="2026-06-05", vehicle_ref="Fleet-P-002", line_type="rebate"),
    ]
    content = synthetic_bp_statement(rows=rows, seed=9).encode("utf-8")

    with pytest.raises(ValidationError) as exc_info:
        await statement_ingest.ingest_statement(
            db_session,
            org.id,
            entity_id=entity.id,
            period="2026-06",
            filename="b.csv",
            content=content,
        )
    assert exc_info.value.code == "unrecognized_fuel_card_statement"
    assert await _fuel_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_2_bp_ingest_statement_cross_tenant_isolation_with_overlapping_data(db_session):
    """Two orgs ingesting the IDENTICAL statement text must never share a
    row — the tenancy-parity overlap discipline (master-context §8)."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_a = await make_entity(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    await _load_pln_rate(db_session)
    content = synthetic_bp_statement(seed=10).encode("utf-8")

    await statement_ingest.ingest_statement(
        db_session,
        org_a.id,
        entity_id=entity_a.id,
        period="2026-06",
        filename="b.csv",
        content=content,
    )
    await db_session.commit()
    await statement_ingest.ingest_statement(
        db_session,
        org_b.id,
        entity_id=entity_b.id,
        period="2026-06",
        filename="b.csv",
        content=content,
    )
    await db_session.commit()

    rows_a = await _fuel_rows(db_session, org_a.id)
    rows_b = await _fuel_rows(db_session, org_b.id)
    assert len(rows_a) == 3
    assert len(rows_b) == 3
    assert {r.id for r in rows_a}.isdisjoint({r.id for r in rows_b})
