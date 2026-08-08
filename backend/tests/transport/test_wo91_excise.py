"""WO-91 — G4.6's diesel excise-duty refund, proven arithmetically.

Every expectation below is a HAND-COMPUTED `Decimal` a reviewer can recompute
with a pencil, because the arithmetic IS the deliverable: this figure goes onto
a spreadsheet a haulier hands to a customs authority.

The harvested mechanism (`BA_fleet_fuel.md` §2.4, §1.3, R42) is
`litres × rate/1,000 L`, per (entity × country), over the validated DIESEL lines
of one accounting month.

WHAT THIS FILE IS DELIBERATELY STRICT ABOUT
---------------------------------------------
* **the one rounding** — the euro is `q2(exact_litres / 1000 * rate)`, so
  1,234.567 L at EUR 30.0000 is exactly EUR 37.04 (37.03701 rounded once), not
  a figure that inherited a rounded litre total;
* **no rate means NO ROW, never a zero** — a country outside the harvested seven
  is reported with its litres in `skipped_countries` and no euro at all;
* **the grain is (entity × country)** — two entities in two countries are four
  cells, each with its own litres;
* **non-diesel never counts** — AdBlue, toll and parking litres are invisible;
* **§4.14/§4.15** — no currency amount is read, so a PLN line and a EUR line
  contribute their litres identically and nothing refuses;
* **§4.19/§3.L** — the analysis writes nothing, asserted column by column.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import PermissionError, ValidationError
from app.core.money import q2
from app.models.fx import FxSource
from app.models.transport.fuel_transaction import PRODUCT_GROUPS, FuelTransaction
from app.services.transport import excise, fuel_ingest, queries
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

PERIOD = "2026-05"
DAY = date(2026, 5, 12)

_SEQ = {"n": 0}


async def _org_entity(db_session, *, name: str = "WO91 Org", seed: int = 1):
    org = await make_org(db_session, name=name)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country="LV", seed=seed)
    await db_session.commit()
    return org.id, entity.id


async def _line(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    country: str,
    qty: Decimal,
    product: str = "DIESEL",
    supplier: str = "Q8",
    period: str = PERIOD,
    txn_date: date = DAY,
    currency: str = "EUR",
    fx_source: str | None = None,
    net_eur: Decimal = Decimal("1500.00"),
) -> FuelTransaction:
    """One validated fuel line. `line_seq` comes from a module counter so the
    WO-50 natural key never collides across a test's many lines."""
    _SEQ["n"] += 1
    vat_eur = q2(net_eur * Decimal("0.21"))
    row = await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=_SEQ["n"],
        country=country,
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=txn_date,
        station="Demo Station Riga",
        product=product,
        qty=qty,
        currency=currency,
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        fx_source=fx_source,
    )
    await db_session.commit()
    return row


def _cell(result, country: str):
    matches = [c for c in result.rows if c.country == country]
    assert len(matches) == 1, f"expected exactly one {country} cell, got {matches}"
    return matches[0]


# --------------------------------------------------------------------------- #
# The registry cut
# --------------------------------------------------------------------------- #


def test_wo91_the_excise_cut_is_org_scoped_diesel_only_and_month_grained():
    """R42: *"over the same validated **diesel** lines"*. The predicate lives in
    the canonical registry, never in the service (R51/WO-85)."""
    compiled = str(queries.excise_transactions("org-1", period=PERIOD))
    assert "fuel_transactions.org_id = :org_id_1" in compiled
    assert "fuel_transactions.product_group = :product_group_1" in compiled
    assert "fuel_transactions.period = :period_1" in compiled
    assert queries.DIESEL in PRODUCT_GROUPS

    scoped = str(queries.excise_transactions("org-1", period=PERIOD, entity_id="e", country="FR"))
    assert "fuel_transactions.entity_id = :entity_id_1" in scoped
    assert "fuel_transactions.country = :country_1" in scoped


# --------------------------------------------------------------------------- #
# The arithmetic
# --------------------------------------------------------------------------- #


async def test_wo91_the_figure_is_litres_over_a_thousand_times_the_rate(db_session):
    """HAND-COMPUTED. 1,234.567 L in FR at the harvested EUR 30.0000/1,000 L:

        1234.567 / 1000 = 1.234567
        1.234567 * 30   = 37.03701
        q2(37.03701)    = 37.04        (ROUND_HALF_UP, applied ONCE)

    The litre total is deliberately not a round number, so a figure computed
    from a quantized litre count (1234.57 -> 37.0371 -> 37.04 by luck, or
    1235 -> 37.05) would be visible.
    """
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="FR", qty=Decimal("1234.567"))

    result = await excise.excise_report(db_session, org_id, period=PERIOD)
    cell = _cell(result, "FR")
    assert cell.litres == Decimal("1234.567")
    assert cell.rate_eur_per_1000l == Decimal("30.0000")
    assert cell.indicative_excise_eur == Decimal("37.04")
    assert result.indicative_excise_eur == Decimal("37.04")
    assert result.litres == Decimal("1234.567")


async def test_wo91_a_cells_litres_are_the_exact_sum_of_its_lines(db_session):
    """Several lines in one cell: the litres add exactly and the euro is
    computed from the TOTAL, not line by line (which would round seven times).

        (400.125 + 600.875 + 0.500) / 1000 * 30 = 1.0015 * 30 = 30.045
        q2(30.045) = 30.05   (ROUND_HALF_UP on a genuine half)
    """
    org_id, entity_id = await _org_entity(db_session)
    for qty in (Decimal("400.125"), Decimal("600.875"), Decimal("0.500")):
        await _line(db_session, org_id, entity_id, country="IT", qty=qty)

    cell = _cell(await excise.excise_report(db_session, org_id, period=PERIOD), "IT")
    assert cell.litres == Decimal("1001.500")
    assert cell.lines == 3
    assert cell.indicative_excise_eur == Decimal("30.05")


async def test_wo91_the_grain_is_entity_times_country(db_session):
    """R42: *"per (entity × country)"*. Two entities across two states are four
    cells, each carrying only its own litres."""
    org = await make_org(db_session, name="WO91 Two Entities")
    await enable_transport(db_session, org.id)
    a = await make_entity(db_session, org.id, country="LV", seed=11)
    b = await make_entity(db_session, org.id, country="EE", seed=12)
    await db_session.commit()

    for entity_id, country, qty in (
        (a.id, "FR", Decimal("1000.000")),
        (a.id, "ES", Decimal("2000.000")),
        (b.id, "FR", Decimal("3000.000")),
        (b.id, "ES", Decimal("4000.000")),
    ):
        await _line(db_session, org.id, entity_id, country=country, qty=qty)

    result = await excise.excise_report(db_session, org.id, period=PERIOD)
    assert len(result.rows) == 4
    grid = {(c.entity_id, c.country): c for c in result.rows}
    assert grid[(a.id, "FR")].litres == Decimal("1000.000")
    assert grid[(a.id, "FR")].indicative_excise_eur == Decimal("30.00")
    assert grid[(b.id, "ES")].litres == Decimal("4000.000")
    assert grid[(b.id, "ES")].indicative_excise_eur == Decimal("120.00")
    # (1000 + 2000 + 3000 + 4000) / 1000 * 30 = 300.00
    assert result.indicative_excise_eur == Decimal("300.00")
    # The entity's own legal name travels with its cell (read via the issuer
    # SERVICE, ADR-P3 rule 2) — a customs packet naming only a UUID is useless.
    assert grid[(a.id, "FR")].entity_name and grid[(a.id, "FR")].entity_name != a.id


async def test_wo91_the_entity_scope_filter_narrows_without_changing_a_figure(db_session):
    """Excise is an ABSOLUTE measure, so scoping cannot move any other cell's
    number — the property that justifies `excise_transactions` having an
    `entity_id` where `price_comparison_transactions` deliberately has none."""
    org = await make_org(db_session, name="WO91 Scope")
    await enable_transport(db_session, org.id)
    a = await make_entity(db_session, org.id, country="LV", seed=21)
    b = await make_entity(db_session, org.id, country="EE", seed=22)
    await db_session.commit()
    await _line(db_session, org.id, a.id, country="FR", qty=Decimal("1000.000"))
    await _line(db_session, org.id, b.id, country="FR", qty=Decimal("5000.000"))

    full = await excise.excise_report(db_session, org.id, period=PERIOD)
    scoped = await excise.excise_report(db_session, org.id, period=PERIOD, entity_id=a.id)
    assert len(scoped.rows) == 1
    assert scoped.rows[0].indicative_excise_eur == Decimal("30.00")
    assert scoped.rows[0].indicative_excise_eur == _cell_for(full, a.id, "FR")

    by_country = await excise.excise_report(db_session, org.id, period=PERIOD, country="FR")
    assert len(by_country.rows) == 2


def _cell_for(result, entity_id: str, country: str) -> Decimal:
    return next(
        c.indicative_excise_eur
        for c in result.rows
        if c.entity_id == entity_id and c.country == country
    )


# --------------------------------------------------------------------------- #
# No rate means NO ROW — never a zero
# --------------------------------------------------------------------------- #


async def test_wo91_a_state_outside_the_regime_yields_no_row_and_no_euro(db_session):
    """The single most important negative in this file. LV is not one of the
    seven states the spec records as operating a commercial-diesel excise
    refund, so its litres produce NO finding — reported honestly as skipped,
    with a litre count and deliberately WITHOUT a euro field."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="LV", qty=Decimal("9000.000"))

    result = await excise.excise_report(db_session, org_id, period=PERIOD)
    assert result.rows == ()
    assert result.indicative_excise_eur == Decimal("0.00")
    assert [s.country for s in result.skipped_countries] == ["LV"]
    assert result.skipped_countries[0].litres == Decimal("9000.000")
    assert result.skipped_countries[0].lines == 1
    assert not hasattr(result.skipped_countries[0], "indicative_excise_eur")
    # The litres were examined, not ignored — the report says so.
    assert result.lines_examined == 1


async def test_wo91_a_mixed_period_reports_the_refunding_states_and_skips_the_rest(db_session):
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="FR", qty=Decimal("1000.000"))
    await _line(db_session, org_id, entity_id, country="LV", qty=Decimal("2000.000"))
    await _line(db_session, org_id, entity_id, country="PL", qty=Decimal("3000.000"))

    result = await excise.excise_report(db_session, org_id, period=PERIOD)
    assert [c.country for c in result.rows] == ["FR"]
    assert result.indicative_excise_eur == Decimal("30.00")
    assert [(s.country, s.litres) for s in result.skipped_countries] == [
        ("LV", Decimal("2000.000")),
        ("PL", Decimal("3000.000")),
    ]


async def test_wo91_a_cell_that_nets_to_zero_litres_produces_no_row(db_session):
    """§4.2 row 9: quantity *"can be 0 (promo correction lines)"*. A cell whose
    litres net to nothing has nothing to file, and a EUR 0.00 line on a customs
    packet is noise at best."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="HR", qty=Decimal("0.000"))

    result = await excise.excise_report(db_session, org_id, period=PERIOD)
    assert result.rows == ()
    assert result.indicative_excise_eur == Decimal("0.00")


# --------------------------------------------------------------------------- #
# Only diesel
# --------------------------------------------------------------------------- #


async def test_wo91_non_diesel_litres_are_never_counted(db_session):
    """R42 / §6.1 — the regime is *"professional diesel"*. AdBlue, toll and
    parking units are not diesel litres and must not reach the figure, or the
    haulier files an overstatement."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="FR", qty=Decimal("1000.000"))
    for product in ("ADBLUE", "TOLL", "PARKING"):
        await _line(
            db_session, org_id, entity_id, country="FR", qty=Decimal("500.000"), product=product
        )

    result = await excise.excise_report(db_session, org_id, period=PERIOD)
    cell = _cell(result, "FR")
    assert cell.litres == Decimal("1000.000")
    assert cell.lines == 1
    assert cell.indicative_excise_eur == Decimal("30.00")
    assert result.lines_examined == 1


async def test_wo91_another_period_is_never_counted(db_session):
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="FR", qty=Decimal("1000.000"))
    await _line(
        db_session,
        org_id,
        entity_id,
        country="FR",
        qty=Decimal("9000.000"),
        period="2026-04",
        txn_date=date(2026, 4, 12),
    )

    cell = _cell(await excise.excise_report(db_session, org_id, period=PERIOD), "FR")
    assert cell.litres == Decimal("1000.000")


# --------------------------------------------------------------------------- #
# The admin override
# --------------------------------------------------------------------------- #


async def test_wo91_an_admin_override_changes_the_figure_and_is_labelled(db_session):
    """R42: *"Rates are admin-overridable"*. 1,000 L at EUR 22.5000/1,000 L is
    EUR 22.50, and the cell says the rate was verified rather than defaulted —
    the distinction §9.2 item 13 exists for."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="FR", qty=Decimal("1000.000"))
    await _line(db_session, org_id, entity_id, country="IT", qty=Decimal("1000.000"))

    before = _cell(await excise.excise_report(db_session, org_id, period=PERIOD), "FR")
    assert before.indicative_excise_eur == Decimal("30.00")
    assert before.rate_is_override is False

    await excise.set_rate(db_session, org_id, country="FR", rate_eur_per_1000l=Decimal("22.5000"))
    await db_session.commit()

    result = await excise.excise_report(db_session, org_id, period=PERIOD)
    after = _cell(result, "FR")
    assert after.rate_eur_per_1000l == Decimal("22.5000")
    assert after.rate_is_override is True
    assert after.indicative_excise_eur == Decimal("22.50")
    # An untouched state keeps the placeholder and says so.
    assert _cell(result, "IT").rate_is_override is False
    assert _cell(result, "IT").indicative_excise_eur == Decimal("30.00")

    await excise.remove_rate(db_session, org_id, country="FR")
    await db_session.commit()
    restored = _cell(await excise.excise_report(db_session, org_id, period=PERIOD), "FR")
    assert restored.rate_is_override is False
    assert restored.indicative_excise_eur == Decimal("30.00")


async def test_wo91_one_orgs_override_never_reaches_another_orgs_figure(db_session):
    """§4.1 over the service path, with IDENTICAL litres in both orgs so only
    tenancy can discriminate."""
    a_org, a_entity = await _org_entity(db_session, name="WO91 Org A", seed=31)
    b_org, b_entity = await _org_entity(db_session, name="WO91 Org B", seed=32)
    await _line(db_session, a_org, a_entity, country="FR", qty=Decimal("1000.000"))
    await _line(db_session, b_org, b_entity, country="FR", qty=Decimal("1000.000"))
    await excise.set_rate(db_session, a_org, country="FR", rate_eur_per_1000l=Decimal("22.5000"))
    await db_session.commit()

    a = await excise.excise_report(db_session, a_org, period=PERIOD)
    b = await excise.excise_report(db_session, b_org, period=PERIOD)
    assert len(a.rows) == 1 and len(b.rows) == 1
    assert a.rows[0].indicative_excise_eur == Decimal("22.50")
    assert b.rows[0].indicative_excise_eur == Decimal("30.00")
    assert b.rows[0].rate_is_override is False
    assert a.rows[0].entity_id == a_entity
    assert b.rows[0].entity_id == b_entity


# --------------------------------------------------------------------------- #
# §4.14 / §4.15 — no currency amount is read, so nothing refuses
# --------------------------------------------------------------------------- #


def test_wo91_no_money_column_is_read_anywhere_in_the_module():
    """STRUCTURAL, the `overcharge_pack` discipline: the amount columns are
    asserted absent BY NAME. This is what makes §4.14 true by construction here
    — nothing can be summed across currencies because nothing currency-shaped is
    ever read."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "app" / "services" / "transport" / "excise.py"
    ).read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]  # skip the module docstring, which DISCUSSES them
    for column in (
        "net_local",
        "vat_local",
        "gross_local",
        "net_eur",
        "vat_eur",
        "net_eur_eff",
    ):
        assert f".{column}" not in body, f"excise.py reads the money column {column}"


async def test_wo91_a_foreign_currency_line_contributes_its_litres_unconverted(db_session):
    """The deliberate divergence from `savings.py`. A PLN line with a RECORDED
    conversion and a EUR line contribute their litres identically, and nothing
    about the currency changes the figure: litres are litres.

        (1000 + 1000) / 1000 * 30 = 60.00
    """
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="FR", qty=Decimal("1000.000"))
    await _line(
        db_session,
        org_id,
        entity_id,
        country="FR",
        qty=Decimal("1000.000"),
        currency="PLN",
        fx_source=FxSource.ecb.value,
    )

    cell = _cell(await excise.excise_report(db_session, org_id, period=PERIOD), "FR")
    assert cell.litres == Decimal("2000.000")
    assert cell.indicative_excise_eur == Decimal("60.00")
    assert cell.lines == 2


async def test_wo91_an_unconvertible_euro_does_not_refuse_the_litre_count(db_session):
    """`savings.py` refuses `fx_source == "unknown"` because it compares EUROS.
    This analysis does not compare euros, so refusing would drop real litres
    from a customs packet for a reason that has nothing to do with litres.

    The row is built in memory and read back through a proxy, exactly as
    WO-87/WO-88 do: after WO-88 the writer and the table CHECK both refuse to
    persist this shape, and a test that could no longer construct its input
    would have to be deleted (§10 forbids that).
    """
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="FR", qty=Decimal("1000.000"))

    poison = FuelTransaction(
        org_id=org_id,
        entity_id=entity_id,
        supplier="ZZZ",
        period=PERIOD,
        line_seq=9999,
        country="FR",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=DAY,
        station="Demo Station Riga",
        product="DIESEL",
        product_group="Diesel",
        qty=Decimal("500.000"),
        currency="PLN",
        net_local=Decimal("1400.00"),
        vat_local=Decimal("294.00"),
        gross_local=Decimal("1694.00"),
        net_eur=Decimal("1400.00"),
        vat_eur=Decimal("294.00"),
        net_eur_eff=Decimal("1400.00"),
        fx_source=FxSource.unknown.value,
    )

    class _Poisoned:
        """A read-through proxy that appends the unstorable row to the
        TRANSACTION query only — the rate query keeps returning its own rows
        untouched, so everything downstream of the fetch is the real code."""

        def __init__(self, real, extra):
            self._real, self._extra = real, extra

        def __getattr__(self, name):
            return getattr(self._real, name)

        async def scalars(self, statement, *args, **kwargs):
            rows = list(await self._real.scalars(statement, *args, **kwargs))
            if statement.column_descriptions[0]["entity"] is FuelTransaction:
                rows.append(self._extra)
            return rows

    result = await excise.excise_report(_Poisoned(db_session, poison), org_id, period=PERIOD)
    cell = _cell(result, "FR")
    assert cell.litres == Decimal("1500.000")
    assert cell.indicative_excise_eur == Decimal("45.00")


# --------------------------------------------------------------------------- #
# The gates
# --------------------------------------------------------------------------- #


async def test_wo91_a_malformed_period_is_refused(db_session):
    org_id, _entity_id = await _org_entity(db_session)
    for bad in ("2026-13", "2026", "2026-Q2", ""):
        with pytest.raises(ValidationError) as exc:
            await excise.excise_report(db_session, org_id, period=bad)
        assert exc.value.code == "invalid_period"
        assert "YYYY-MM" in str(exc.value)


async def test_wo91_a_malformed_country_is_refused(db_session):
    org_id, _entity_id = await _org_entity(db_session)
    with pytest.raises(ValidationError) as exc:
        await excise.excise_report(db_session, org_id, period=PERIOD, country="FRA")
    assert exc.value.code == "invalid_country"


async def test_wo91_the_report_is_module_gated(db_session):
    org = await make_org(db_session, name="WO91 Report Module Off")
    await db_session.commit()
    with pytest.raises(PermissionError) as exc:
        await excise.excise_report(db_session, org.id, period=PERIOD)
    assert exc.value.code == "module_not_enabled"


async def test_wo91_an_empty_period_is_an_empty_report_not_an_error(db_session):
    org_id, _entity_id = await _org_entity(db_session)
    result = await excise.excise_report(db_session, org_id, period=PERIOD)
    assert result.rows == ()
    assert result.skipped_countries == ()
    assert result.litres == Decimal("0")
    assert result.indicative_excise_eur == Decimal("0.00")
    assert result.lines_examined == 0


# --------------------------------------------------------------------------- #
# §4.19 / §3.L — the analysis writes nothing
# --------------------------------------------------------------------------- #


async def test_wo91_the_analysis_mutates_no_transaction(db_session):
    """§3.L requires this seam to be *"structurally incapable of gating or
    mutating a legal figure"*. Every column of every row is compared before and
    after a run — the `contract_audit`/`savings` proof, applied here."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, country="FR", qty=Decimal("1234.567"))
    await _line(db_session, org_id, entity_id, country="LV", qty=Decimal("500.000"))

    columns = [c.name for c in FuelTransaction.__table__.columns]

    def snapshot(rows):
        return sorted(tuple(getattr(r, c) for c in columns) for r in rows)

    before = snapshot(
        await db_session.scalars(select(FuelTransaction).where(FuelTransaction.org_id == org_id))
    )
    await excise.excise_report(db_session, org_id, period=PERIOD)
    db_session.expire_all()
    after = snapshot(
        await db_session.scalars(select(FuelTransaction).where(FuelTransaction.org_id == org_id))
    )
    assert before == after
