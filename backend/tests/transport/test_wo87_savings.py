"""WO-87 — G4.7's overpay / benchmark analyses, proven arithmetically.

Every expectation below is a HAND-COMPUTED `Decimal` a reviewer can recompute
with a pencil, because the analytic correctness IS the deliverable: a same-day
overpay figure that is off by a rounding is a number a client takes into a
supplier negotiation.

The fixture prices are chosen so every €/L is exact in decimal (no repeating
quotients) and every euro lands on a clean cent, so a failure is a real
behavioural change rather than a rounding argument.

WHAT THIS FILE IS DELIBERATELY STRICT ABOUT
---------------------------------------------
* **the exact-tie boundary** — equal €/L is NOT an overpay (positive deltas
  only), asserted as an empty findings tuple rather than a €0.00 finding;
* **no false positive from a lonely day** — one supplier that day means there
  was no rival, which is not the same fact as "you were competitive";
* **§4.14 scoping** — a cheaper supplier in another country never becomes
  anyone's rival;
* **§4.15 refusal** — a line with no established EUR basis refuses, on BOTH
  sides of the boundary (a recorded conversion is compared normally);
* **R52** — the two grains are computed over different denominators and this
  file asserts they genuinely differ on the same data;
* **§4.19** — the analyses write nothing, asserted column-by-column.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.core.money import q2
from app.models.fx import FxSource
from app.models.transport.fuel_transaction import PRODUCT_GROUPS, FuelTransaction
from app.services.transport import fuel_ingest, queries, savings
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

# No module-level `pytest.mark.asyncio`: `pytest.ini` sets `asyncio_mode = auto`,
# so async tests are collected without it — and this file deliberately mixes
# async service tests with SYNC unit tests over the registry's pure builders,
# which the blanket mark would (correctly) warn about.

PERIOD = "2026-05"
D1 = date(2026, 5, 12)
D2 = date(2026, 5, 13)


async def _org_entity(db_session, *, name: str = "WO87 Org", seed: int = 1):
    org = await make_org(db_session, name=name)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country="LV", seed=seed)
    await db_session.commit()
    return org.id, entity.id


_SEQ = {"n": 0}


async def _line(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    supplier: str,
    country: str = "LV",
    txn_date: date = D1,
    qty: Decimal = Decimal("1000.000"),
    net_eur: Decimal = Decimal("1500.00"),
    net_eur_eff: Decimal | None = None,
    product: str = "DIESEL",
    period: str = PERIOD,
    currency: str = "EUR",
    fx_source: str | None = None,
) -> FuelTransaction:
    """One validated fuel line. `line_seq` is issued from a module counter so
    the natural key never collides across a test's many lines."""
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
        net_eur_eff=net_eur_eff,
        fx_source=fx_source,
    )
    await db_session.commit()
    return row


# --------------------------------------------------------------------------- #
# The diesel-only predicate lives in the registry and matches a real group
# --------------------------------------------------------------------------- #


def test_wo87_the_unknown_fx_source_literal_matches_the_platform_enum():
    """`savings.FX_SOURCE_UNKNOWN` is a LITERAL, not an import: a transport
    service may only import `app.models.transport`/`app.models.base` (ADR-0023
    rule 2, `tests/test_boundaries.py`), which is why `rebate.py` states its own
    `fx_source` values the same way. A test file has no such restriction, so the
    two are pinned together here — a drift would silently stop the §4.15 gate
    recognising the one value that means "this euro was never converted"."""
    assert savings.FX_SOURCE_UNKNOWN == FxSource.unknown.value


def test_wo87_the_diesel_predicate_names_a_real_product_group():
    """`queries.DIESEL` is the row-selection predicate §2.5 states as "diesel
    only". A cut that matched nothing would read as "no supplier ever
    overcharged you" — the most dangerous wrong answer this family can give —
    so the constant is pinned against the model's own tuple."""
    assert queries.DIESEL in PRODUCT_GROUPS


def test_wo87_the_price_comparison_cut_is_org_scoped_and_diesel_only():
    stmt = queries.price_comparison_transactions("org-1", period=PERIOD)
    compiled = str(stmt)
    assert "fuel_transactions.org_id = :org_id_1" in compiled
    assert "fuel_transactions.product_group = :product_group_1" in compiled
    params = stmt.compile().params
    assert params["org_id_1"] == "org-1"
    assert params["product_group_1"] == "Diesel"
    assert params["period_1"] == PERIOD


def test_wo87_the_price_comparison_cut_takes_no_supplier_argument():
    """Filtering the ROWS by supplier would change who the cheapest rival was,
    so the same query would silently answer a different question. The absence is
    asserted rather than merely not written."""
    import inspect

    params = inspect.signature(queries.price_comparison_transactions).parameters
    assert "supplier" not in params
    assert set(params) == {"org_id", "period", "country"}


# --------------------------------------------------------------------------- #
# 1. Avoidable overpay (same-day) — the hand-computed figure
# --------------------------------------------------------------------------- #


async def test_wo87_same_day_overpay_is_litres_times_the_gap_to_the_cheapest_rival(db_session):
    """§2.5 row 1, computed by hand.

    LV, 2026-05-12, three suppliers, all diesel, all EUR:

        AAA  1,000.000 L  net_eur_eff 1,500.00  ->  1.5000 EUR/L
        BBB  2,000.000 L  net_eur_eff 2,900.00  ->  1.4500 EUR/L
        CCC    500.000 L  net_eur_eff   700.00  ->  1.4000 EUR/L

    The cheapest RIVAL excludes the supplier itself, so:

        AAA vs CCC: (1.5000 - 1.4000) x 1,000.000 =  100.00
        BBB vs CCC: (1.4500 - 1.4000) x 2,000.000 =  100.00
        CCC vs BBB: (1.4000 - 1.4500) < 0          ->  no finding

    Total avoidable = 200.00 EUR.
    """
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="BBB",
        qty=Decimal("2000.000"),
        net_eur=Decimal("2900.00"),
    )
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="CCC",
        qty=Decimal("500.000"),
        net_eur=Decimal("700.00"),
    )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert result.avoidable_eur == Decimal("200.00")
    assert [f.supplier for f in result.findings] == ["AAA", "BBB"]

    aaa, bbb = result.findings
    assert aaa.eur_l_eff == Decimal("1.5000")
    assert aaa.cheapest_rival_supplier == "CCC"
    assert aaa.cheapest_rival_eur_l_eff == Decimal("1.4000")
    assert aaa.delta_eur_l == Decimal("0.1000")
    assert aaa.avoidable_eur == Decimal("100.00")
    assert aaa.litres == Decimal("1000.000")
    assert aaa.suppliers_that_day == 3

    assert bbb.cheapest_rival_supplier == "CCC"
    assert bbb.delta_eur_l == Decimal("0.0500")
    assert bbb.avoidable_eur == Decimal("100.00")

    assert result.days_compared == 1
    assert result.days_without_a_rival == 0
    assert result.lines_compared == 3
    assert result.grain == savings.GRAIN_SAME_DAY
    assert result.currency == "EUR"
    assert result.price_basis == savings.PRICE_BASIS
    assert result.legal_framing == savings.LEGAL_FRAMING


async def test_wo87_an_exact_tie_is_not_an_overpay(db_session):
    """ "Positive deltas only" (verbatim). Two suppliers at an identical €/L
    produce NO finding — not a €0.00 finding, which would read on a negotiation
    sheet as "this supplier overcharged you (by nothing)".

    The comparison is made on the UNROUNDED quotients, so the boundary cannot
    move on a presentation rounding: 3,000.00 / 2,000.000 and 1,500.00 /
    1,000.000 are the same exact Decimal 1.5, reached from different numerators.
    """
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="BBB",
        qty=Decimal("2000.000"),
        net_eur=Decimal("3000.00"),
    )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert result.findings == ()
    assert result.by_supplier == ()
    assert result.avoidable_eur == Decimal("0.00")
    # The day WAS compared — there were two suppliers. That is a different fact
    # from "there was nobody to compare against".
    assert result.days_compared == 1
    assert result.days_without_a_rival == 0


async def test_wo87_one_cent_above_the_tie_is_an_overpay(db_session):
    """The other side of the same boundary: the smallest gap that survives the
    €0.01 quantization IS reported. 3,000.10 / 2,000.000 = 1.50005 EUR/L, so the
    delta is 0.00005 EUR/L over 2,000 L = 0.10 EUR exactly."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="BBB",
        qty=Decimal("2000.000"),
        net_eur=Decimal("3000.10"),
    )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert [f.supplier for f in result.findings] == ["BBB"]
    assert result.findings[0].avoidable_eur == Decimal("0.10")
    assert result.avoidable_eur == Decimal("0.10")


async def test_wo87_a_day_with_no_rival_yields_no_finding(db_session):
    """ "Requires >=2 suppliers that day" (verbatim). A single supplier's day is
    counted in `days_without_a_rival` and produces nothing — never a false
    positive, and never a silent zero that reads as "competitive"."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("9999.00"))

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert result.findings == ()
    assert result.avoidable_eur == Decimal("0.00")
    assert result.days_compared == 0
    assert result.days_without_a_rival == 1


async def test_wo87_a_rival_on_a_different_day_is_not_a_rival(db_session):
    """The comparison is SAME-DAY. A cheaper supplier the following day does not
    make yesterday an overpay — that is the whole reason the grain is a day and
    not a month (which is what the internal benchmark is for)."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session, org_id, entity_id, supplier="AAA", txn_date=D1, net_eur=Decimal("1500.00")
    )
    await _line(
        db_session, org_id, entity_id, supplier="BBB", txn_date=D2, net_eur=Decimal("1400.00")
    )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert result.findings == ()
    assert result.days_without_a_rival == 2
    assert result.days_compared == 0


async def test_wo87_the_comparison_never_crosses_a_country(db_session):
    """§4.14 / §2.5's "same-country" clause. A cheaper supplier in EE is not a
    rival to a supplier in LV — fuel duty, VAT regime and pump price differ by
    country, so the two €/L are not like-for-like even though both are EUR."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session, org_id, entity_id, supplier="AAA", country="LV", net_eur=Decimal("1500.00")
    )
    await _line(
        db_session, org_id, entity_id, supplier="BBB", country="EE", net_eur=Decimal("1000.00")
    )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert result.findings == ()
    assert result.days_without_a_rival == 2  # (LV, D1) and (EE, D1), each alone


async def test_wo87_a_country_scope_filter_does_not_change_any_finding(db_session):
    """Narrowing to one country is a SCOPE filter, not a maths change: the
    groups are keyed on country, so restricting them cannot alter any group's
    contents. Asserted rather than assumed, because it is the one filter this
    surface offers and the reason it is safe when a supplier filter is not."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session, org_id, entity_id, supplier="AAA", country="LV", net_eur=Decimal("1500.00")
    )
    await _line(
        db_session, org_id, entity_id, supplier="CCC", country="LV", net_eur=Decimal("1400.00")
    )
    await _line(
        db_session, org_id, entity_id, supplier="AAA", country="EE", net_eur=Decimal("1600.00")
    )
    await _line(
        db_session, org_id, entity_id, supplier="CCC", country="EE", net_eur=Decimal("1300.00")
    )

    everywhere = await savings.same_day_overpay(db_session, org_id, period=PERIOD)
    lv_only = await savings.same_day_overpay(db_session, org_id, period=PERIOD, country="LV")

    lv_from_all = [f for f in everywhere.findings if f.country == "LV"]
    assert [(f.supplier, f.avoidable_eur) for f in lv_from_all] == [
        (f.supplier, f.avoidable_eur) for f in lv_only.findings
    ]
    assert lv_only.country == "LV"
    assert lv_only.avoidable_eur == Decimal("100.00")  # (1.5000 - 1.4000) x 1,000
    assert everywhere.avoidable_eur == Decimal("400.00")  # + (1.6000 - 1.3000) x 1,000


async def test_wo87_a_non_diesel_line_is_not_in_the_comparison(db_session):
    """ "Diesel only" (verbatim). An AdBlue line's €/L is not a diesel price, and
    letting it in would make it the cheapest "rival" of the day and suppress
    every real finding in that country."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="BBB",
        product="ADBLUE",
        net_eur=Decimal("400.00"),
    )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert result.findings == ()
    assert result.lines_compared == 1
    assert result.days_without_a_rival == 1


async def test_wo87_zero_litre_lines_are_skipped_and_counted(db_session):
    """§4.2 row 9 — quantity "can be 0 (promo correction lines)". A €/L is
    undefined for such a line; treating it as a €0.00 price would make it the
    cheapest rival of every day it appears in."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(db_session, org_id, entity_id, supplier="CCC", net_eur=Decimal("1400.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="ZZZ",
        qty=Decimal("0.000"),
        net_eur=Decimal("0.00"),
    )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert result.lines_skipped_zero_qty == 1
    assert result.lines_compared == 2
    assert [f.supplier for f in result.findings] == ["AAA"]
    assert result.findings[0].cheapest_rival_supplier == "CCC"


async def test_wo87_a_supplier_days_price_is_volume_weighted(db_session):
    """Documented interpretation 2. AAA fills twice on the same day:

        200.000 L at net_eur_eff 320.00  (1.6000 EUR/L)
        800.000 L at net_eur_eff 1,120.00 (1.4000 EUR/L)

    A simple mean of the two prices would be 1.5000; the volume-weighted price
    is 1,440.00 / 1,000.000 = 1.4400. Against a rival at 1.4000 the overpay is
    (1.4400 - 1.4000) x 1,000.000 = 40.00, not the 100.00 a simple mean gives.
    """
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="AAA",
        qty=Decimal("200.000"),
        net_eur=Decimal("320.00"),
    )
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="AAA",
        qty=Decimal("800.000"),
        net_eur=Decimal("1120.00"),
    )
    await _line(db_session, org_id, entity_id, supplier="CCC", net_eur=Decimal("1400.00"))

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert [f.supplier for f in result.findings] == ["AAA"]
    finding = result.findings[0]
    assert finding.eur_l_eff == Decimal("1.4400")
    assert finding.litres == Decimal("1000.000")
    assert finding.lines == 2
    assert finding.avoidable_eur == Decimal("40.00")


async def test_wo87_the_effective_price_is_what_is_compared_not_the_invoiced_one(db_session):
    """R49 — "every comparison/ordering uses the effective price". AAA invoices
    at 1.6000 EUR/L but its off-invoice rebate brings it to 1.3000 effective; it
    is therefore CHEAPER than a rival invoicing 1.4000 flat, and the rival is the
    one flagged. Comparing on `net_eur` would invert the answer entirely — which
    is exactly what §4.2's Q8/Port One case is about."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="AAA",
        net_eur=Decimal("1600.00"),
        net_eur_eff=Decimal("1300.00"),
    )
    await _line(db_session, org_id, entity_id, supplier="BBB", net_eur=Decimal("1400.00"))

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert [f.supplier for f in result.findings] == ["BBB"]
    assert result.findings[0].cheapest_rival_supplier == "AAA"
    assert result.findings[0].cheapest_rival_eur_l_eff == Decimal("1.3000")
    assert result.findings[0].avoidable_eur == Decimal("100.00")


async def test_wo87_the_rollup_reconciles_with_the_findings(db_session):
    """The per-supplier attribution is rolled up from the SAME findings the
    caller sees, so a summary total and a detail total can never disagree."""
    org_id, entity_id = await _org_entity(db_session)
    for day in (D1, D2):
        await _line(
            db_session, org_id, entity_id, supplier="AAA", txn_date=day, net_eur=Decimal("1500.00")
        )
        await _line(
            db_session, org_id, entity_id, supplier="CCC", txn_date=day, net_eur=Decimal("1400.00")
        )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert len(result.findings) == 2
    assert len(result.by_supplier) == 1
    total = result.by_supplier[0]
    assert total.country == "LV"
    assert total.supplier == "AAA"
    assert total.days == 2
    assert total.litres == Decimal("2000.000")
    assert total.avoidable_eur == Decimal("200.00")
    assert total.avoidable_eur == result.avoidable_eur


# --------------------------------------------------------------------------- #
# 2. Internal benchmark — R52's second grain
# --------------------------------------------------------------------------- #


async def test_wo87_internal_benchmark_is_country_month_best_of_your_own_suppliers(db_session):
    """§2.5 row 2, computed by hand. LV over the whole month:

        AAA  1,000.000 L @ 1.5000  ->  gap 0.1000 -> 100.00
        BBB  2,000.000 L @ 1.4500  ->  gap 0.0500 -> 100.00
        CCC    500.000 L @ 1.4000  ->  gap 0.0000 ->   0.00  (the best)

    Total = 200.00, and CCC appears on the sheet at zero because it is the
    supplier the volume would be ROUTED TO.
    """
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="BBB",
        qty=Decimal("2000.000"),
        net_eur=Decimal("2900.00"),
    )
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="CCC",
        qty=Decimal("500.000"),
        net_eur=Decimal("700.00"),
    )

    result = await savings.internal_benchmark(db_session, org_id, period=PERIOD)

    assert result.benchmark_gap_eur == Decimal("200.00")
    assert [(r.supplier, r.gap_eur_l, r.benchmark_gap_eur) for r in result.rows] == [
        ("AAA", Decimal("0.1000"), Decimal("100.00")),
        ("BBB", Decimal("0.0500"), Decimal("100.00")),
        ("CCC", Decimal("0.0000"), Decimal("0.00")),
    ]
    assert all(r.best_supplier == "CCC" for r in result.rows)
    assert all(r.best_eur_l_eff == Decimal("1.4000") for r in result.rows)
    assert result.countries_compared == 1
    assert result.suppliers_compared == 3
    assert result.grain == savings.GRAIN_COUNTRY_MONTH
    assert result.price_basis == savings.PRICE_BASIS
    assert result.legal_framing == savings.LEGAL_FRAMING


async def test_wo87_internal_benchmark_never_crosses_a_country(db_session):
    """The grain is country × month. A cheaper EE supplier is not the LV best."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session, org_id, entity_id, supplier="AAA", country="LV", net_eur=Decimal("1500.00")
    )
    await _line(
        db_session, org_id, entity_id, supplier="BBB", country="EE", net_eur=Decimal("1000.00")
    )

    result = await savings.internal_benchmark(db_session, org_id, period=PERIOD)

    assert result.benchmark_gap_eur == Decimal("0.00")
    assert {r.country: r.best_supplier for r in result.rows} == {"LV": "AAA", "EE": "BBB"}
    assert result.countries_compared == 2


async def test_wo87_the_two_grains_do_not_reconcile_and_say_so(db_session):
    """R52's acceptance, made concrete on ONE fixture.

    Two suppliers, two days, same country:

        day 1: AAA 1,000 L @ 1.5000 | CCC 1,000 L @ 1.4000
        day 2: AAA 1,000 L @ 1.4000 | CCC 1,000 L @ 1.5000

    SAME-DAY grain: AAA overpays 100.00 on day 1, CCC overpays 100.00 on day 2
    => 200.00.
    MONTH grain: both suppliers average 1.4500 EUR/L over 2,000 L, so nobody is
    above the country's best and the figure is 0.00.

    Both are correct. They are different questions, they carry different grain
    labels, and their euro fields are named differently so nothing can add them.
    """
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session, org_id, entity_id, supplier="AAA", txn_date=D1, net_eur=Decimal("1500.00")
    )
    await _line(
        db_session, org_id, entity_id, supplier="CCC", txn_date=D1, net_eur=Decimal("1400.00")
    )
    await _line(
        db_session, org_id, entity_id, supplier="AAA", txn_date=D2, net_eur=Decimal("1400.00")
    )
    await _line(
        db_session, org_id, entity_id, supplier="CCC", txn_date=D2, net_eur=Decimal("1500.00")
    )

    same_day = await savings.same_day_overpay(db_session, org_id, period=PERIOD)
    monthly = await savings.internal_benchmark(db_session, org_id, period=PERIOD)

    assert same_day.avoidable_eur == Decimal("200.00")
    assert monthly.benchmark_gap_eur == Decimal("0.00")
    assert same_day.avoidable_eur != monthly.benchmark_gap_eur
    assert same_day.grain != monthly.grain
    # The euro fields are named differently on purpose — a consumer cannot add
    # them without writing two different attribute names.
    assert not hasattr(same_day, "benchmark_gap_eur")
    assert not hasattr(monthly, "avoidable_eur")


# --------------------------------------------------------------------------- #
# 3. Expected rebate — §2.5's learning loop
# --------------------------------------------------------------------------- #


async def test_wo87_expected_rebate_learns_the_typical_and_flags_only_the_line_that_lost_it(
    db_session,
):
    """§2.5 row 8, computed by hand.

    Q8/LV history, 1,000.000 L per line, invoiced 1,500.00:

        2026-03  net_eur_eff 1,450.00  -> applied 0.0500 EUR/L
        2026-04  net_eur_eff 1,440.00  -> applied 0.0600 EUR/L
        2026-05  net_eur_eff 1,430.00  -> applied 0.0700 EUR/L
        2026-05  net_eur_eff 1,500.00  -> applied 0.0000  <-- MISSING

    Median of the three rebate-bearing lines = 0.0600 EUR/L, so the flagged
    line's advisory magnitude is 0.0600 x 1,000.000 = 60.00 EUR.
    """
    org_id, entity_id = await _org_entity(db_session)
    for period, eff in (
        ("2026-03", Decimal("1450.00")),
        ("2026-04", Decimal("1440.00")),
        (PERIOD, Decimal("1430.00")),
    ):
        await _line(
            db_session,
            org_id,
            entity_id,
            supplier="Q8",
            period=period,
            net_eur=Decimal("1500.00"),
            net_eur_eff=eff,
        )
    missing = await _line(
        db_session,
        org_id,
        entity_id,
        supplier="Q8",
        period=PERIOD,
        net_eur=Decimal("1500.00"),
        net_eur_eff=Decimal("1500.00"),
    )

    result = await savings.expected_rebate(db_session, org_id, period=PERIOD)

    assert [
        (e.supplier, e.country, e.typical_eur_l, e.learned_from_lines) for e in result.expectations
    ] == [("Q8", "LV", Decimal("0.0600"), 3)]
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.fuel_transaction_id == missing.id
    assert finding.applied_eur_l == Decimal("0.0000")
    assert finding.typical_eur_l == Decimal("0.0600")
    assert finding.expected_rebate_eur == Decimal("60.00")
    assert finding.eur_l_doc == Decimal("1.5000")
    assert finding.eur_l_eff == Decimal("1.5000")
    assert finding.learned_from_lines == 3
    assert result.expected_rebate_eur == Decimal("60.00")
    assert result.lines_examined == 2
    assert result.lines_with_a_rebate == 1
    assert result.lines_without_an_expectation == 0
    assert result.tolerance_eur_l == Decimal("0.005")
    assert result.legal_framing == savings.LEGAL_FRAMING


async def test_wo87_the_median_is_robust_to_one_freak_line(db_session):
    """A MEDIAN, not a mean. Rebates of 0.0500, 0.0600, 0.0700 and one freak
    1.0000 give a mean of 0.2950 — an expectation no line would ever meet — but
    a median of 0.0650, which is what the pair actually grants."""
    org_id, entity_id = await _org_entity(db_session)
    for period, eff in (
        ("2026-01", Decimal("1450.00")),
        ("2026-02", Decimal("1440.00")),
        ("2026-03", Decimal("1430.00")),
        ("2026-04", Decimal("500.00")),
    ):
        await _line(
            db_session,
            org_id,
            entity_id,
            supplier="Q8",
            period=period,
            net_eur=Decimal("1500.00"),
            net_eur_eff=eff,
        )
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="Q8",
        period=PERIOD,
        net_eur=Decimal("1500.00"),
        net_eur_eff=Decimal("1500.00"),
    )

    result = await savings.expected_rebate(db_session, org_id, period=PERIOD)

    # sorted: 0.05, 0.06, 0.07, 1.00 -> median (0.06 + 0.07) / 2 = 0.065
    assert result.expectations[0].typical_eur_l == Decimal("0.0650")
    assert result.findings[0].expected_rebate_eur == Decimal("65.00")


async def test_wo87_a_pair_with_no_rebate_history_is_silent(db_session):
    """ "Fails toward silence": with no history there is no expectation, and
    crying wolf on every ordinary supplier would train an operator to ignore the
    one flag that matters (`rebate.missing_source_warnings`' stated posture,
    applied at line grain)."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(db_session, org_id, entity_id, supplier="BBB", net_eur=Decimal("1400.00"))

    result = await savings.expected_rebate(db_session, org_id, period=PERIOD)

    assert result.expectations == ()
    assert result.findings == ()
    assert result.expected_rebate_eur == Decimal("0.00")
    assert result.lines_without_an_expectation == 2


async def test_wo87_the_expectation_is_learned_per_supplier_and_country(db_session):
    """The pair grain is `(supplier, country)`, verbatim. Q8's LV rebate says
    nothing about Q8 in EE — a rebate is negotiated per network per country."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="Q8",
        country="LV",
        period="2026-04",
        net_eur=Decimal("1500.00"),
        net_eur_eff=Decimal("1440.00"),
    )
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="Q8",
        country="LV",
        net_eur=Decimal("1500.00"),
        net_eur_eff=Decimal("1500.00"),
    )
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="Q8",
        country="EE",
        net_eur=Decimal("1500.00"),
        net_eur_eff=Decimal("1500.00"),
    )

    result = await savings.expected_rebate(db_session, org_id, period=PERIOD)

    assert [(e.supplier, e.country) for e in result.expectations] == [("Q8", "LV")]
    assert [(f.supplier, f.country) for f in result.findings] == [("Q8", "LV")]
    assert result.lines_without_an_expectation == 1  # the EE line


async def test_wo87_a_rebate_just_over_the_tolerance_is_not_missing(db_session):
    """`abs(rebate) < 0.005` (verbatim), on both sides of the boundary.

    Over 1,000.000 L, a 5.00 EUR rebate is exactly 0.0050 EUR/L — NOT missing —
    while a 4.90 EUR rebate is 0.0049 EUR/L and IS.
    """
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="Q8",
        period="2026-04",
        net_eur=Decimal("1500.00"),
        net_eur_eff=Decimal("1440.00"),
    )
    on_boundary = await _line(
        db_session,
        org_id,
        entity_id,
        supplier="Q8",
        net_eur=Decimal("1500.00"),
        net_eur_eff=Decimal("1495.00"),
    )
    below = await _line(
        db_session,
        org_id,
        entity_id,
        supplier="Q8",
        net_eur=Decimal("1500.00"),
        net_eur_eff=Decimal("1495.10"),
    )

    result = await savings.expected_rebate(db_session, org_id, period=PERIOD)

    flagged = {f.fuel_transaction_id for f in result.findings}
    assert below.id in flagged
    assert on_boundary.id not in flagged


async def test_wo87_expected_rebate_scopes_to_one_supplier_without_changing_it(db_session):
    """The supplier filter IS safe here (unlike on the same-day comparison):
    each pair's expectation is learned independently, so narrowing the scope
    cannot change any other pair's figure."""
    org_id, entity_id = await _org_entity(db_session)
    for supplier in ("Q8", "AAA"):
        await _line(
            db_session,
            org_id,
            entity_id,
            supplier=supplier,
            period="2026-04",
            net_eur=Decimal("1500.00"),
            net_eur_eff=Decimal("1440.00"),
        )
        await _line(
            db_session,
            org_id,
            entity_id,
            supplier=supplier,
            net_eur=Decimal("1500.00"),
            net_eur_eff=Decimal("1500.00"),
        )

    everyone = await savings.expected_rebate(db_session, org_id, period=PERIOD)
    just_q8 = await savings.expected_rebate(db_session, org_id, period=PERIOD, supplier="Q8")

    assert len(everyone.findings) == 2
    assert [f.supplier for f in just_q8.findings] == ["Q8"]
    q8_from_all = next(f for f in everyone.findings if f.supplier == "Q8")
    assert q8_from_all.expected_rebate_eur == just_q8.findings[0].expected_rebate_eur


# --------------------------------------------------------------------------- #
# §4.15 — the EUR-basis gate, both sides
# --------------------------------------------------------------------------- #


async def test_wo87_a_line_with_no_established_eur_basis_refuses(db_session):
    """§4.15 — refuse rather than guess, and refuse rather than quietly shrink
    the comparison set. A PLN line with no recorded conversion at all makes ALL
    THREE analyses refuse, because a comparison whose set silently lost a
    supplier answers a different question than the one asked."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="BBB",
        currency="PLN",
        net_eur=Decimal("1400.00"),
        fx_source=None,
    )

    for call in (
        savings.same_day_overpay,
        savings.internal_benchmark,
        savings.expected_rebate,
    ):
        with pytest.raises(ValidationError) as exc:
            await call(db_session, org_id, period=PERIOD)
        assert exc.value.code == "fx_rate_unavailable"
        assert "PLN" in str(exc.value)


async def test_wo87_an_explicit_unknown_fx_source_refuses_even_in_eur(db_session):
    """`fx_source == "unknown"` is the writer positively recording that no rate
    was available (`app/models/fx.py`). Whatever sits in `net_eur_eff` is not a
    converted euro, so it is refused regardless of what the currency column
    says."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="BBB",
        net_eur=Decimal("1400.00"),
        fx_source="unknown",
    )

    with pytest.raises(ValidationError) as exc:
        await savings.same_day_overpay(db_session, org_id, period=PERIOD)
    assert exc.value.code == "fx_rate_unavailable"


async def test_wo87_a_recorded_conversion_is_compared_in_eur(db_session):
    """The other side of the gate. The SAME PLN line, now carrying `ecb`
    provenance, is compared normally — the analysis converts nothing itself, it
    reads the EUR figure ingestion already resolved, which is what §4.14's
    "without a recorded conversion" clause permits."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="BBB",
        currency="PLN",
        net_eur=Decimal("1400.00"),
        fx_source="ecb",
    )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)

    assert [f.supplier for f in result.findings] == ["AAA"]
    assert result.findings[0].cheapest_rival_supplier == "BBB"
    assert result.avoidable_eur == Decimal("100.00")
    assert result.currency == "EUR"


async def test_wo87_a_eur_line_with_no_fx_provenance_is_fine(db_session):
    """EUR is the identity and involves no rate, so a NULL `fx_source` on a EUR
    line is not a missing conversion. Asserted explicitly because the opposite
    reading would refuse every line `fuel_ingest` has ever written (its
    `fx_source` argument defaults to `None`)."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(
        db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"), fx_source=None
    )
    await _line(
        db_session, org_id, entity_id, supplier="CCC", net_eur=Decimal("1400.00"), fx_source=None
    )

    result = await savings.same_day_overpay(db_session, org_id, period=PERIOD)
    assert result.avoidable_eur == Decimal("100.00")


# --------------------------------------------------------------------------- #
# Refusals, read-only posture, org scoping
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["2026-13", "2026", "26-05", "2026-5", ""])
async def test_wo87_a_malformed_period_is_refused(db_session, bad):
    """Fails CLOSED: silently analysing an empty set for a typo'd month looks
    exactly like "you never overpaid anyone"."""
    org_id, _ = await _org_entity(db_session)
    with pytest.raises(ValidationError) as exc:
        await savings.same_day_overpay(db_session, org_id, period=bad)
    assert exc.value.code == "invalid_period"


async def test_wo87_a_malformed_country_is_refused(db_session):
    org_id, _ = await _org_entity(db_session)
    with pytest.raises(ValidationError) as exc:
        await savings.same_day_overpay(db_session, org_id, period=PERIOD, country="LVA")
    assert exc.value.code == "invalid_country"


async def test_wo87_the_analyses_write_nothing(db_session):
    """§4.19 / R41's "read-only over the analytics" posture, asserted the way
    `contract_audit`'s is: every transaction row compared column-by-column
    before and after all three analyses run."""
    org_id, entity_id = await _org_entity(db_session)
    await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(
        db_session,
        org_id,
        entity_id,
        supplier="CCC",
        net_eur=Decimal("1400.00"),
        net_eur_eff=Decimal("1380.00"),
    )

    columns = [c.name for c in FuelTransaction.__table__.columns]

    async def snapshot() -> dict[str, dict[str, object]]:
        rows = list(await db_session.scalars(select(FuelTransaction)))
        return {r.id: {c: getattr(r, c) for c in columns} for r in rows}

    before = await snapshot()
    await savings.same_day_overpay(db_session, org_id, period=PERIOD)
    await savings.internal_benchmark(db_session, org_id, period=PERIOD)
    await savings.expected_rebate(db_session, org_id, period=PERIOD)
    await db_session.commit()

    assert await snapshot() == before


async def test_wo87_a_second_tenants_identical_rows_are_never_in_scope(db_session):
    """§4.1/§4.4 — the two orgs carry IDENTICAL-looking data (same suppliers,
    same day, same litres, same euros), so a missing tenant filter could not
    pass by accident: org A would see six lines instead of three and the
    cheapest rival would be another tenant's supplier."""
    org_a, entity_a = await _org_entity(db_session, name="WO87 Org A", seed=11)
    org_b, entity_b = await _org_entity(db_session, name="WO87 Org B", seed=12)

    for org_id, entity_id in ((org_a, entity_a), (org_b, entity_b)):
        await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
        await _line(db_session, org_id, entity_id, supplier="CCC", net_eur=Decimal("1400.00"))
    # Only B has a third, much cheaper supplier. If A's comparison leaked, A's
    # rival would become ZZZ and its figure would jump.
    await _line(db_session, org_b, entity_b, supplier="ZZZ", net_eur=Decimal("1000.00"))

    a = await savings.same_day_overpay(db_session, org_a, period=PERIOD)
    b = await savings.same_day_overpay(db_session, org_b, period=PERIOD)

    assert a.lines_compared == 2
    assert [f.cheapest_rival_supplier for f in a.findings] == ["CCC"]
    assert a.avoidable_eur == Decimal("100.00")

    assert b.lines_compared == 3
    assert {f.cheapest_rival_supplier for f in b.findings} == {"ZZZ"}
    assert b.avoidable_eur == Decimal("900.00")  # (0.5 x 1000) + (0.4 x 1000)
