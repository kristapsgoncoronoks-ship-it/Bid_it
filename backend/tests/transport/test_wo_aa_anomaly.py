"""WO-AA — six anomaly rules whose bounds are learned, never fixed (R54).

THE PROPERTY THAT MATTERS MOST IS SCALE INVARIANCE
----------------------------------------------------
`BA_fleet_fuel.md` §2.5 states the design rule — *"NO absolute price
thresholds ever, every bound is learned from the data's own spread, because
fuel prices swing"* — and the harvest (`VAT_HARVEST.md`) states the test that
proves it: **double every price and the same rows flag.**

That is a far better gate than counting findings. A rule with a hidden
constant in it passes any row-count assertion you write on today's data and
fails the day fuel moves; it fails scale invariance immediately, on the same
fixture, forever. So the first test here doubles every price and asserts the
flagged set is IDENTICAL, and the seeded-violation check for this order is an
absolute threshold spliced into one rule.

WHAT ELSE THESE PIN
-------------------
- Each of the six rules fires on its own case AND stays quiet on the near-miss
  beside it. A rule that flags everything satisfies "it fires" and is useless.
- The volume floors suppress a small-litre outlier, because a 15-litre top-up
  makes a wild €/L out of ordinary rounding.
- `price_divergence` ignores a market that moved TOGETHER — the distinction
  §2.5 draws in its own words, and the one an implementation is most likely to
  get wrong by flagging any supplier that moved at all.
- A rule that could not run says so (`suppressed`) rather than returning
  silence, because "nothing was unusual" and "I could not tell" are different
  answers and only one of them is reassuring.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.transport.fuel_transaction import FuelTransaction
from app.services.transport import anomaly
from app.services.transport.anomaly import (
    OFF_HOURS,
    OFF_PERIOD,
    PRICE_DIVERGENCE,
    STATION_PRICE,
    VEHICLE_PRICE,
    VOLUME_SPIKE,
)

PERIOD = "2026-05"
PRIOR = "2026-04"


def _txn(
    *,
    supplier: str = "Eurowag",
    station: str = "Demo Station Riga",
    vehicle: str = "TRK-001",
    country: str = "LV",
    qty: str = "1000.000",
    eur_per_l: str = "1.4000",
    period: str = PERIOD,
    txn_date: date = date(2026, 5, 12),
    txn_time: str | None = "09:30",
    line_seq: int = 1,
) -> FuelTransaction:
    """One in-memory row. Never added to a session: every rule under test is a
    pure function over rows, which is what lets the scale-invariance property
    be checked on the same fixture twice without touching a database."""
    litres = Decimal(qty)
    net = (Decimal(eur_per_l) * litres).quantize(Decimal("0.01"))
    return FuelTransaction(
        org_id="org",
        entity_id="entity",
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country=country,
        vehicle_ref=vehicle,
        txn_date=txn_date,
        txn_time=txn_time,
        station=station,
        product="DIESEL",
        product_group="Diesel",
        qty=litres,
        currency="EUR",
        net_local=net,
        vat_local=Decimal("0.00"),
        gross_local=net,
        net_eur=net,
        vat_eur=Decimal("0.00"),
        net_eur_eff=net,
        fx_source="eur",
    )


def _doubled(rows: list[FuelTransaction]) -> list[FuelTransaction]:
    """The same fleet of fills with every PRICE doubled and every LITRE
    unchanged — the harvest's own transformation."""
    out = []
    for r in rows:
        rate = (Decimal(r.net_eur_eff) / Decimal(r.qty)) * 2
        out.append(
            _txn(
                supplier=r.supplier,
                station=r.station,
                vehicle=r.vehicle_ref,
                country=r.country,
                qty=str(r.qty),
                eur_per_l=str(rate),
                period=r.period,
                txn_date=r.txn_date,
                txn_time=r.txn_time,
                line_seq=r.line_seq,
            )
        )
    return out


#: Nine ordinary stations and one dear one. The size is deliberate — see
#: `test_a_lone_extreme_in_a_tiny_population_masks_itself` for what happens
#: below it, which is a property of the specified statistic and not a bug.
_ORDINARY = (
    "1.4000",
    "1.4100",
    "1.3900",
    "1.4050",
    "1.3950",
    "1.4020",
    "1.3980",
    "1.4010",
    "1.4030",
)
_DEAR = "1.7500"


def _fixture() -> list[FuelTransaction]:
    """A country priced like a country: a tight band of stations, and one that
    is clearly dearer than the rest of them."""
    rows = []
    for i, rate in enumerate([*_ORDINARY, _DEAR]):
        rows.append(
            _txn(station=f"Station {i + 1}", vehicle=f"TRK-{i:03d}", eur_per_l=rate, line_seq=i + 1)
        )
    return rows


def _dear_station() -> str:
    return f"Station {len(_ORDINARY) + 1}"


def _keys(found: list[anomaly.Anomaly]) -> set[tuple[str, str]]:
    return {(a.rule, a.subject) for a in found}


# --------------------------------------------------------------------------- #
# R54 — the property the whole order exists to satisfy
# --------------------------------------------------------------------------- #


def test_doubling_every_price_flags_exactly_the_same_rows():
    """The harvest's own test, verbatim: *"Double every price ⇒ the same rows
    are flagged."* An absolute threshold anywhere fails this on the spot."""
    rows = _fixture()
    once = _keys(anomaly.detect_station_price(rows)) | _keys(anomaly.detect_vehicle_price(rows))
    twice = _keys(anomaly.detect_station_price(_doubled(rows))) | _keys(
        anomaly.detect_vehicle_price(_doubled(rows))
    )

    assert once, "the fixture must flag something, or this proves nothing"
    assert once == twice, (
        "scale invariance broken — a bound in these rules is absolute, not learned: "
        f"{once} vs {twice}"
    )


def test_scale_invariance_holds_for_divergence_too():
    """The rule with the most room for a hidden constant: it compares MOVES,
    and a naive implementation flags 'moved by more than X cents'."""
    prior = [
        _txn(supplier=s, station=f"S{i}", eur_per_l=r, period=PRIOR, line_seq=i)
        for i, (s, r) in enumerate(
            [("A", "1.4000"), ("B", "1.4000"), ("C", "1.4000"), ("D", "1.4000")], start=1
        )
    ]
    now = [
        _txn(supplier=s, station=f"S{i}", eur_per_l=r, line_seq=i)
        for i, (s, r) in enumerate(
            [("A", "1.4500"), ("B", "1.4500"), ("C", "1.4500"), ("D", "1.9000")], start=1
        )
    ]
    once = _keys(anomaly.detect_price_divergence(now, prior))
    twice = _keys(anomaly.detect_price_divergence(_doubled(now), _doubled(prior)))
    assert once, "the fixture must flag something"
    assert once == twice


# --------------------------------------------------------------------------- #
# Each rule fires, and stays quiet on the near miss
# --------------------------------------------------------------------------- #


def test_station_price_flags_the_dear_station_and_not_its_neighbours():
    found = anomaly.detect_station_price(_fixture())
    assert _keys(found) == {(STATION_PRICE, _dear_station())}
    # The evidence travels with the verdict.
    assert found[0].expected is not None
    assert found[0].observed > found[0].expected


def test_a_station_below_the_litre_floor_is_not_priced():
    """A 15-litre top-up makes a wild €/L out of ordinary rounding. The floor
    is on LITRES, which is why it does not violate R54."""
    rows = _fixture()
    rows.append(_txn(station="Tiny Stop", qty="15.000", eur_per_l="3.0000", line_seq=99))
    flagged = {a.subject for a in anomaly.detect_station_price(rows)}
    assert "Tiny Stop" not in flagged, "a sub-floor station was priced anyway"
    assert _dear_station() in flagged, "the real outlier stopped being found"


def test_price_divergence_ignores_a_market_that_moved_together():
    """§2.5's distinction, and the one most easily lost: a supplier that MOVED
    is not anomalous; a supplier that moved differently FROM THE MARKET is."""
    prior = [
        _txn(supplier=s, station=f"S{i}", eur_per_l="1.4000", period=PRIOR, line_seq=i)
        for i, s in enumerate(["A", "B", "C", "D"], start=1)
    ]
    # Everybody rises four cents. The market rose; nobody diverged.
    together = [
        _txn(supplier=s, station=f"S{i}", eur_per_l="1.4400", line_seq=i)
        for i, s in enumerate(["A", "B", "C", "D"], start=1)
    ]
    assert anomaly.detect_price_divergence(together, prior) == []


def test_volume_spike_uses_the_vehicles_own_history():
    """Robust by design: the spike must not be able to hide inside the mean it
    would be measured against."""
    rows = [
        _txn(vehicle="TRK-9", qty=q, line_seq=i, station=f"S{i}")
        for i, q in enumerate(["100.000", "105.000", "98.000", "102.000", "900.000"], start=1)
    ]
    found = anomaly.detect_volume_spike(rows)
    assert [a.subject for a in found] == ["TRK-9"]
    assert found[0].observed == Decimal("900.000")


def test_off_period_flags_a_date_outside_the_month_it_was_loaded_into():
    rows = [
        _txn(txn_date=date(2026, 5, 3), line_seq=1),
        _txn(txn_date=date(2026, 4, 28), line_seq=2),  # loaded into 2026-05
    ]
    found = anomaly.detect_off_period(rows)
    assert len(found) == 1
    assert found[0].line_seq == 2
    # Categorical: there is no spread here, so no distance is invented.
    assert found[0].deviation is None


def test_off_hours_covers_the_window_and_nothing_either_side():
    inside = ["22:00", "23:30", "00:15", "04:59"]
    outside = ["21:59", "05:00", "12:00"]
    for t in inside:
        assert anomaly.detect_off_hours([_txn(txn_time=t)]), f"{t} should be off-hours"
    for t in outside:
        assert not anomaly.detect_off_hours([_txn(txn_time=t)]), f"{t} should not be"


def test_a_line_with_no_usable_clock_is_not_guessed_at():
    """`txn_time` is free text a statement printed, so "missing" and "garbled"
    both mean the same thing here: nothing to judge. Treating either as
    midnight would manufacture card-misuse findings out of absent data — and
    midnight is INSIDE the window, so the guess would not even be neutral."""
    for clock in (None, "", "   ", "n/a", "99:00"):
        assert anomaly.detect_off_hours([_txn(txn_time=clock)]) == [], clock


# --------------------------------------------------------------------------- #
# Honest silence
# --------------------------------------------------------------------------- #


def test_too_few_peers_says_nothing_rather_than_flagging_one_of_two():
    """Two points always sit exactly 1σ from their own mean. A population that
    small cannot support the question."""
    rows = [
        _txn(station="Only A", eur_per_l="1.4000", line_seq=1),
        _txn(station="Only B", eur_per_l="9.9000", line_seq=2),
    ]
    assert anomaly.detect_station_price(rows) == []


def test_an_identical_population_has_no_outlier():
    rows = [_txn(station=f"S{i}", eur_per_l="1.4000", line_seq=i) for i in range(1, 6)]
    assert anomaly.detect_station_price(rows) == []


def test_the_modified_z_declines_to_score_a_constant_series():
    """MAD is zero there, and the honest answer to "how unusual is this among
    identical numbers" is that the question does not apply — not a huge score
    that would flag every departure from a constant."""
    assert anomaly._modified_z(Decimal("5"), [Decimal("5")] * 5) is None


def test_the_rule_vocabulary_is_closed_and_complete():
    """Six rules, and the module exposes exactly those six: a screen renders
    this list, and an operator learns it."""
    assert set(anomaly.RULES) == {
        STATION_PRICE,
        PRICE_DIVERGENCE,
        VOLUME_SPIKE,
        VEHICLE_PRICE,
        OFF_PERIOD,
        OFF_HOURS,
    }
    assert len(anomaly.RULES) == 6


def test_the_constants_are_the_harvested_ones():
    """Pinned because they are the specification, not a preference: §2.5 fixes
    2.0σ, a modified-z cutoff of 3.5, and the 200/100 L floors."""
    assert anomaly.ANOMALY_SIGMAS == Decimal("2.0")
    assert anomaly.MODIFIED_Z_CUTOFF == Decimal("3.5")
    assert anomaly.STATION_LITRE_FLOOR == Decimal("200")
    assert anomaly.VEHICLE_LITRE_FLOOR == Decimal("100")


@pytest.mark.asyncio
async def test_divergence_is_suppressed_with_a_reason_when_there_is_no_prior_month(
    db_session,
):
    """A rule that COULD NOT run is not a rule that found nothing. A screen
    showing both as silence would tell the operator the month was clean."""
    from tests.transport.conftest import enable_transport, make_org

    org = await make_org(db_session, name="WO-AA Org")
    await enable_transport(db_session, org.id)
    await db_session.commit()

    result = await anomaly.detect(db_session, org.id, period=PERIOD)
    assert [rule for rule, _ in result.suppressed] == [PRICE_DIVERGENCE]
    assert "month before" in result.suppressed[0][1]


def test_a_lone_extreme_in_a_tiny_population_masks_itself():
    """A real property of the specified statistic, recorded rather than hidden.

    `station_price` is σ-based because §2.5 says so. In a population of five
    with ONE extreme member, that member inflates the very σ it is then
    measured against, and can end up just inside its own threshold — the
    masking effect that is the whole reason §2.5 specifies the ROBUST statistic
    for `volume_spike`, where the population is a single vehicle's own short
    history and masking is guaranteed rather than possible.

    This is not an argument for changing the rule: on a country's real station
    list the population is large enough that one dear station cannot move the
    spread much, which is exactly the case `_fixture` models. It is an argument
    for knowing the limit, so nobody later "fixes" a quiet small-population
    month by reaching for an absolute threshold — the one thing R54 forbids.
    """
    tiny = [
        _txn(station=f"S{i}", vehicle=f"V{i}", eur_per_l=rate, line_seq=i)
        for i, rate in enumerate(["1.4000", "1.4100", "1.3900", "1.4050", "1.9500"], start=1)
    ]
    assert anomaly.detect_station_price(tiny) == []

    # The same shape of outlier IS found once the population can carry it.
    assert anomaly.detect_station_price(_fixture())


# --------------------------------------------------------------------------- #
# The route
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_route_returns_the_six_rule_vocabulary_and_the_suppressions(
    auth_client, db_session
):
    """A thin controller over the service: the screen must be able to label and
    group findings without hard-coding a rule list of its own that could drift
    from the server's."""
    from sqlalchemy import select

    from app.models.organization import Organization
    from app.services import modules as modules_svc

    org_id = await db_session.scalar(select(Organization.id))
    await modules_svc.set_enabled(db_session, org_id, "transport", True)
    await db_session.commit()

    r = await auth_client.get("/api/v1/transport/savings/anomalies?period=2026-05")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == "2026-05"
    assert body["rules"] == list(anomaly.RULES)
    assert body["count"] == 0
    # An empty month still reports WHY divergence could not run.
    assert [s["rule"] for s in body["suppressed"]] == [PRICE_DIVERGENCE]


@pytest.mark.asyncio
async def test_the_route_refuses_a_bad_period_rather_than_analysing_an_empty_set(
    auth_client, db_session
):
    """`savings.validate_period`'s recorded reasoning, inherited: silently
    analysing an empty set for a typo'd month looks exactly like "nothing was
    unusual", which is a materially different and far more comforting answer
    than "that is not a period"."""
    from sqlalchemy import select

    from app.models.organization import Organization
    from app.services import modules as modules_svc

    org_id = await db_session.scalar(select(Organization.id))
    await modules_svc.set_enabled(db_session, org_id, "transport", True)
    await db_session.commit()

    r = await auth_client.get("/api/v1/transport/savings/anomalies?period=2026-13")
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_period"


@pytest.mark.asyncio
async def test_the_module_gate_fails_closed(auth_client):
    """Transport is entitlement-gated; an org without it gets a refusal naming
    the module, not an empty report that would read as a clean month."""
    r = await auth_client.get("/api/v1/transport/savings/anomalies?period=2026-05")
    assert r.status_code == 403
    assert r.json()["code"] == "module_not_enabled"
