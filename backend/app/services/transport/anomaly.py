"""Fuel anomaly detection (G4.7 §2.5 row 7, R54) — six rules, no fixed prices.

WHY THIS IS ONE ORDER AND NOT SIX SLICES
------------------------------------------
`WO-87-overpay-benchmark.md` reserved this deliberately: *"six rules,
`ANOMALY_SIGMAS = 2.0`, modified-z 3.5, volume floors. A whole order of its
own; half-building two of six rules would be worse than none."* An operator
handed two rules would read a quiet screen as "nothing unusual happened",
which is a stronger claim than two rules can support.

THE DESIGN RULE, AND WHY IT IS ALSO THE TEST
----------------------------------------------
R54, verbatim from §2.5: **no absolute price thresholds, ever** — every bound
is learned from the data's own spread, *"because fuel prices swing"*. A rule
that says "flag anything over €2.10/L" is correct for one quarter and wrong
for the next, and nobody notices the day it goes wrong: it just stops firing,
or fires on everything.

That gives this module an unusually exact certification, and the harvest states
it as a test rather than a principle: **double every price and exactly the same
rows flag.** Any hidden constant fails it immediately, which is why the suite
gates on that property rather than on how many rows came back.

The volume floors (200 L for a station, 100 L for a vehicle) are NOT a
counter-example. They are thresholds on LITRES, not on price, and R54 names
them itself. Their job is to stop a 15-litre top-up from setting a station's
€/L: a small denominator makes a wild rate out of ordinary rounding.

WHICH STATISTIC, AND WHY EACH
-------------------------------
Two are specified, and they are not interchangeable:

- **mean + kσ** (`ANOMALY_SIGMAS = 2.0`) for the price rules
  (`station_price`, `vehicle_price`, `price_divergence`). These compare a unit
  against a population of peers — many stations in a country, many vehicles in
  a fleet, many suppliers in a market — where the spread itself is the thing
  being measured.
- **Robust modified z (Iglewicz–Hoaglin), cutoff 3.5** for `volume_spike`.
  That rule compares a vehicle against ITS OWN trailing volumes: a short,
  self-referential series where one genuine spike inflates the mean and the
  standard deviation it would be tested against, and hides itself. Median and
  MAD do not move like that, which is the whole reason the harvest specifies a
  robust statistic for exactly this rule.

A note on `price_divergence` that is easy to get wrong, and which §2.5 calls
out: it flags a supplier whose month-on-month move DIVERGES from the market's
median move — not one that merely moved. When every supplier in a country goes
up four cents, nothing anomalous happened; that is the market. The subject is
the residual after the market's own movement is taken out.

EVERYTHING IS DERIVED — NO TABLE, NO STORED VERDICT
-----------------------------------------------------
Every rule computes from `fuel_transactions` rows that already exist, the same
choice WO-Q made for supplier reliability. A stored anomaly would be a verdict
frozen against a population that keeps changing: add next month's fills and the
same row may no longer be unusual. Nothing here is written down, so nothing can
go stale.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionError
from app.models.transport.fuel_transaction import FuelTransaction
from app.services import modules
from app.services.transport import queries, savings

# --------------------------------------------------------------------------- #
# The constants the harvest fixes. Every one is a SHAPE parameter (how far from
# the crowd) or a VOLUME floor — never a price.
# --------------------------------------------------------------------------- #

#: §2.5: how many standard deviations from the peer mean counts as unusual.
ANOMALY_SIGMAS = Decimal("2.0")

#: Iglewicz–Hoaglin modified z cutoff, §2.5's own figure.
MODIFIED_Z_CUTOFF = Decimal("3.5")

#: The constant in the Iglewicz–Hoaglin statistic: 0.6745 is the 0.75 quantile
#: of the standard normal, which is what makes MAD a consistent estimator of σ
#: for normal data. Written out because a bare 0.6745 in the formula reads like
#: a tuning knob, and it is not one.
MAD_SCALE = Decimal("0.6745")

#: Volume floors, §2.5. Litres, not euros — see the module docstring.
STATION_LITRE_FLOOR = Decimal("200")
VEHICLE_LITRE_FLOOR = Decimal("100")

#: §2.5's card-misuse window: diesel bought at 22:00–04:59, as HOURS.
#:
#: Hours rather than `datetime.time` because `FuelTransaction.txn_time` is a
#: VARCHAR — statements print a clock in whatever shape their network uses, and
#: the column keeps what was printed. Comparing that string against a `time`
#: silently compares a string to an object; mypy caught exactly that here
#: before any test could.
OFF_HOURS_FROM_HOUR = 22
OFF_HOURS_TO_HOUR = 5

#: €/L working precision, restated from `savings.RATE_EXP` rather than imported
#: so a change there cannot silently redefine an anomaly's arithmetic.
RATE_EXP = Decimal("0.0001")

# The six rule codes. A closed vocabulary: a screen renders these, and an
# operator learns them.
STATION_PRICE = "station_price"
PRICE_DIVERGENCE = "price_divergence"
VOLUME_SPIKE = "volume_spike"
VEHICLE_PRICE = "vehicle_price"
OFF_PERIOD = "off_period"
OFF_HOURS = "off_hours"
RULES = (
    STATION_PRICE,
    PRICE_DIVERGENCE,
    VOLUME_SPIKE,
    VEHICLE_PRICE,
    OFF_PERIOD,
    OFF_HOURS,
)

#: A population smaller than this has no spread worth measuring — two points
#: always sit exactly 1σ from their own mean, so every pair would flag one
#: member. Suppressed rather than reported, the same reasoning
#: `PEER_MIN_CONTRIBUTORS` applies to the peer benchmark.
MIN_POPULATION = 3


@dataclass(frozen=True)
class Anomaly:
    """One flagged observation.

    `observed` and `expected` are carried so the screen can show the reader the
    two numbers the verdict came from. A flag whose evidence is not visible
    beside it is a machine asserting something, which is exactly what the
    reliability design refused: the band is rendered next to the rule that
    produced it."""

    rule: str
    subject: str  # the station, vehicle, or supplier the finding is about
    country: str | None
    observed: Decimal
    expected: Decimal | None
    #: How far out, in the rule's own units (σ for the mean-based rules, the
    #: modified-z score for `volume_spike`). None where the rule is categorical
    #: (`off_period`, `off_hours`) and distance has no meaning.
    deviation: Decimal | None
    litres: Decimal
    detail: str
    line_seq: int | None = None
    txn_date: date | None = None


@dataclass(frozen=True)
class AnomalyResult:
    period: str
    anomalies: tuple[Anomaly, ...]
    #: Rules that ran but had too little data to say anything, with the reason.
    #: A rule that could not run is NOT the same as a rule that found nothing,
    #: and a screen that showed both as silence would be overclaiming.
    suppressed: tuple[tuple[str, str], ...] = ()

    @property
    def count(self) -> int:
        return len(self.anomalies)


# --------------------------------------------------------------------------- #
# Statistics. Decimal throughout — this module compares money-derived rates,
# and the codebase's money rule does not stop being true because a number is
# an intermediate.
# --------------------------------------------------------------------------- #


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _stdev(values: list[Decimal]) -> Decimal:
    """Population standard deviation.

    Population rather than sample: the stations of a country in a month ARE the
    population being described, not a draw from a larger one. Bessel's
    correction would be answering a question nobody asked here.
    """
    mu = _mean(values)
    variance = sum(((v - mu) ** 2 for v in values), Decimal(0)) / Decimal(len(values))
    return variance.sqrt()


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _modified_z(value: Decimal, values: list[Decimal]) -> Decimal | None:
    """Iglewicz–Hoaglin modified z of `value` within `values`.

    Returns None when the MAD is zero — a series with no dispersion at all. The
    formula divides by it, and the honest answer to "how unusual is this within
    a set of identical numbers" is that the question does not apply. Returning
    a large score there would flag every departure from a constant series,
    however small, which is the opposite of robust.
    """
    med = _median(values)
    mad = _median([abs(v - med) for v in values])
    if mad == 0:
        return None
    return MAD_SCALE * (value - med) / mad


def _rate(net_eur_eff: Decimal, qty: Decimal) -> Decimal | None:
    """Effective €/L — R49's basis, `net_eur_eff / qty`, never `net_eur`."""
    if qty <= 0:
        return None
    return (Decimal(net_eur_eff) / Decimal(qty)).quantize(RATE_EXP)


# --------------------------------------------------------------------------- #
# The six rules. Each takes rows and returns findings; none of them reads the
# database, so each is testable on a list.
# --------------------------------------------------------------------------- #


def _weighted_rate(rows: list[FuelTransaction]) -> tuple[Decimal, Decimal] | None:
    """(volume-weighted €/L, total litres) for a group, or None below floor.

    Volume-weighted, never a mean of rates: a 50-litre fill would otherwise
    move a station's price as much as a 40,000-litre one — the same defect
    §2.5 calls out for the margin report's pack average.
    """
    litres = sum((Decimal(r.qty) for r in rows), Decimal(0))
    if litres <= 0:
        return None
    net = sum((Decimal(r.net_eur_eff) for r in rows), Decimal(0))
    return (net / litres).quantize(RATE_EXP), litres


def detect_station_price(rows: list[FuelTransaction]) -> list[Anomaly]:
    """A station charging above its country's own spread (≥200 L floor)."""
    out: list[Anomaly] = []
    by_country: dict[str, dict[str, list[FuelTransaction]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.station:
            by_country[r.country][r.station].append(r)

    for country, stations in by_country.items():
        scored: dict[str, tuple[Decimal, Decimal]] = {}
        for station, group in stations.items():
            got = _weighted_rate(group)
            if got is None or got[1] < STATION_LITRE_FLOOR:
                continue  # below the floor: too little volume to price a station
            scored[station] = got
        if len(scored) < MIN_POPULATION:
            continue
        rates = [v[0] for v in scored.values()]
        mu, sigma = _mean(rates), _stdev(rates)
        if sigma == 0:
            continue  # every station identical — nothing is an outlier
        threshold = mu + ANOMALY_SIGMAS * sigma
        for station, (rate, litres) in scored.items():
            if rate > threshold:
                out.append(
                    Anomaly(
                        rule=STATION_PRICE,
                        subject=station,
                        country=country,
                        observed=rate,
                        expected=mu.quantize(RATE_EXP),
                        deviation=((rate - mu) / sigma).quantize(Decimal("0.01")),
                        litres=litres,
                        detail=(
                            f"{station} averaged {rate} €/L against a {country} mean of "
                            f"{mu.quantize(RATE_EXP)} €/L across {len(scored)} stations."
                        ),
                    )
                )
    return out


def detect_vehicle_price(rows: list[FuelTransaction]) -> list[Anomaly]:
    """A vehicle paying above the FLEET's spread (≥100 L floor)."""
    out: list[Anomaly] = []
    by_vehicle: dict[str, list[FuelTransaction]] = defaultdict(list)
    for r in rows:
        if r.vehicle_ref:
            by_vehicle[r.vehicle_ref].append(r)

    scored: dict[str, tuple[Decimal, Decimal]] = {}
    for vehicle, group in by_vehicle.items():
        got = _weighted_rate(group)
        if got is None or got[1] < VEHICLE_LITRE_FLOOR:
            continue
        scored[vehicle] = got
    if len(scored) < MIN_POPULATION:
        return out
    rates = [v[0] for v in scored.values()]
    mu, sigma = _mean(rates), _stdev(rates)
    if sigma == 0:
        return out
    threshold = mu + ANOMALY_SIGMAS * sigma
    for vehicle, (rate, litres) in scored.items():
        if rate > threshold:
            out.append(
                Anomaly(
                    rule=VEHICLE_PRICE,
                    subject=vehicle,
                    country=None,  # a vehicle is compared against the whole fleet
                    observed=rate,
                    expected=mu.quantize(RATE_EXP),
                    deviation=((rate - mu) / sigma).quantize(Decimal("0.01")),
                    litres=litres,
                    detail=(
                        f"{vehicle} averaged {rate} €/L against a fleet mean of "
                        f"{mu.quantize(RATE_EXP)} €/L across {len(scored)} vehicles."
                    ),
                )
            )
    return out


def detect_price_divergence(
    rows: list[FuelTransaction], prior: list[FuelTransaction]
) -> list[Anomaly]:
    """A supplier whose month-on-month move DIVERGES from the market's.

    Not a supplier that moved. When every supplier rises four cents, the market
    rose four cents and nothing happened; the subject of this rule is what is
    left after the market's own movement is removed.
    """
    out: list[Anomaly] = []
    now: dict[str, list[FuelTransaction]] = defaultdict(list)
    was: dict[str, list[FuelTransaction]] = defaultdict(list)
    for r in rows:
        now[r.supplier].append(r)
    for r in prior:
        was[r.supplier].append(r)

    moves: dict[str, Decimal] = {}
    for supplier, group in now.items():
        before = was.get(supplier)
        if not before:
            continue  # no prior month: no move to speak of, not a zero move
        a, b = _weighted_rate(before), _weighted_rate(group)
        if a is None or b is None:
            continue
        moves[supplier] = b[0] - a[0]
    if len(moves) < MIN_POPULATION:
        return out

    values = list(moves.values())
    market = _median(values)  # the MARKET move, §2.5's own word
    sigma = _stdev(values)
    if sigma == 0:
        return out
    for supplier, move in moves.items():
        distance = abs(move - market)
        if distance > ANOMALY_SIGMAS * sigma:
            litres = _weighted_rate(now[supplier])
            out.append(
                Anomaly(
                    rule=PRICE_DIVERGENCE,
                    subject=supplier,
                    country=None,
                    observed=move.quantize(RATE_EXP),
                    expected=market.quantize(RATE_EXP),
                    deviation=(distance / sigma).quantize(Decimal("0.01")),
                    litres=litres[1] if litres else Decimal(0),
                    detail=(
                        f"{supplier} moved {move.quantize(RATE_EXP)} €/L while the market "
                        f"median moved {market.quantize(RATE_EXP)} €/L."
                    ),
                )
            )
    return out


def detect_volume_spike(rows: list[FuelTransaction]) -> list[Anomaly]:
    """A vehicle's fill against ITS OWN trailing volumes (robust modified z).

    Self-referential and short, which is precisely why §2.5 specifies the
    robust statistic here: one genuine spike inflates the very mean and
    deviation it would otherwise be measured against, and disappears into them.
    """
    out: list[Anomaly] = []
    by_vehicle: dict[str, list[FuelTransaction]] = defaultdict(list)
    for r in rows:
        if r.vehicle_ref and Decimal(r.qty) > 0:
            by_vehicle[r.vehicle_ref].append(r)

    for vehicle, group in by_vehicle.items():
        if len(group) < MIN_POPULATION:
            continue
        volumes = [Decimal(r.qty) for r in group]
        for row in group:
            score = _modified_z(Decimal(row.qty), volumes)
            if score is None or score <= MODIFIED_Z_CUTOFF:
                continue
            out.append(
                Anomaly(
                    rule=VOLUME_SPIKE,
                    subject=vehicle,
                    country=row.country,
                    observed=Decimal(row.qty),
                    expected=_median(volumes),
                    deviation=score.quantize(Decimal("0.01")),
                    litres=Decimal(row.qty),
                    detail=(
                        f"{vehicle} took {row.qty} L against its own typical {_median(volumes)} L."
                    ),
                    line_seq=row.line_seq,
                    txn_date=row.txn_date,
                )
            )
    return out


def detect_off_period(rows: list[FuelTransaction]) -> list[Anomaly]:
    """A transaction dated outside the accounting month it was loaded into.

    Categorical, so it carries no deviation: a date either falls in the period
    or it does not, and dressing that up as a distance would imply a spread
    that is not there.
    """
    out: list[Anomaly] = []
    for r in rows:
        stamped = f"{r.txn_date.year:04d}-{r.txn_date.month:02d}"
        if stamped != r.period:
            out.append(
                Anomaly(
                    rule=OFF_PERIOD,
                    subject=r.supplier,
                    country=r.country,
                    observed=Decimal(r.qty),
                    expected=None,
                    deviation=None,
                    litres=Decimal(r.qty),
                    detail=(f"Dated {r.txn_date.isoformat()} but loaded into {r.period}."),
                    line_seq=r.line_seq,
                    txn_date=r.txn_date,
                )
            )
    return out


def _hour_of(clock: str | None) -> int | None:
    """The hour from a printed clock, or None when there isn't one.

    `txn_time` is free text from a statement (`"08:12"`, `"08:12:00"`, and
    whatever a future network prints), so this reads the leading hour and
    refuses anything that is not one. A garbled clock is treated exactly like a
    missing clock — no guess — because inventing an hour here would manufacture
    card-misuse findings out of a formatting quirk.
    """
    if not clock:
        return None
    head = clock.strip().split(":", 1)[0]
    if not head.isdigit():
        return None
    hour = int(head)
    return hour if 0 <= hour <= 23 else None


def detect_off_hours(rows: list[FuelTransaction]) -> list[Anomaly]:
    """Diesel bought 22:00–04:59 — §2.5's possible card misuse.

    Advisory by nature and by wording: night driving is legal and routine on
    long hauls. The finding is that somebody should look, never that somebody
    did something.
    """
    out: list[Anomaly] = []
    for r in rows:
        hour = _hour_of(r.txn_time)
        if hour is None:
            continue  # no usable clock: nothing to judge, and no guess
        if not (hour >= OFF_HOURS_FROM_HOUR or hour < OFF_HOURS_TO_HOUR):
            continue
        out.append(
            Anomaly(
                rule=OFF_HOURS,
                subject=r.vehicle_ref or r.supplier,
                country=r.country,
                observed=Decimal(r.qty),
                expected=None,
                deviation=None,
                litres=Decimal(r.qty),
                detail=f"Diesel at {r.txn_time} on {r.txn_date.isoformat()}.",
                line_seq=r.line_seq,
                txn_date=r.txn_date,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# The service entry point
# --------------------------------------------------------------------------- #


def _prior_period(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"


async def detect(
    db: AsyncSession, org_id: str, *, period: str, country: str | None = None
) -> AnomalyResult:
    """Run all six rules over one accounting month.

    All six or none: a caller cannot ask for a subset, because a screen showing
    two rules' silence as "nothing unusual" would be claiming more than two
    rules can support — the reason this was reserved as one order.
    """
    savings.validate_period(period)
    # `is_enabled`, never `require_enabled`: a service signals with `AppError`,
    # not `fastapi.HTTPException` (ADR-P3 rule 3). Fails CLOSED.
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    rows = list(
        await db.scalars(
            queries.price_comparison_transactions(org_id, period=period, country=country)
        )
    )
    prior = list(
        await db.scalars(
            queries.price_comparison_transactions(
                org_id, period=_prior_period(period), country=country
            )
        )
    )

    found: list[Anomaly] = []
    suppressed: list[tuple[str, str]] = []

    found.extend(detect_station_price(rows))
    found.extend(detect_vehicle_price(rows))
    found.extend(detect_volume_spike(rows))
    found.extend(detect_off_period(rows))
    found.extend(detect_off_hours(rows))
    if prior:
        found.extend(detect_price_divergence(rows, prior))
    else:
        suppressed.append(
            (
                PRICE_DIVERGENCE,
                f"No {_prior_period(period)} transactions — a month-on-month move "
                "needs the month before it.",
            )
        )

    found.sort(key=lambda a: (a.rule, a.subject, a.line_seq or 0))
    return AnomalyResult(period=period, anomalies=tuple(found), suppressed=tuple(suppressed))
