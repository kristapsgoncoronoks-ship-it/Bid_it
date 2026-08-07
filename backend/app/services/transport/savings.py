"""The overpay / benchmark analyses — negotiation evidence, never a debt
(G4.7; R52, R53, R49; `BA_fleet_fuel.md` §2.4, §2.5, §3.G G1).

THE THREE HARVESTED DEFINITIONS, VERBATIM
-------------------------------------------
`BA_fleet_fuel.md` §2.5's table is the whole business rule for each analysis in
this module. Quoted in full, because each clause maps to a symbol below:

**1. Avoidable overpay (same-day)**

    "litres × (this supplier's eff €/L − the cheapest same-day, same-country
     rival's eff €/L), diesel only, requires ≥2 suppliers that day, positive
     deltas only. Attributed to the country of supply and the supplier that
     charged the premium."

**2. Internal benchmark**

    "Σ_supplier qty × (supplier_eff − best_eff) per country × month = 'money you
     could have saved by routing volume to the cheaper supplier you were ALREADY
     using.' Self-sourced ⇒ no antitrust exposure."

**3. Expected rebate**

    "Learns the typical €/L rebate per (supplier, country) from all history;
     flags a line where it is missing (abs(rebate) < 0.005). Rationale: catches
     rebates that arrive on a separate invoice we often don't see (the Q8/Port
     One case)."

R52 governs the first two together: *"Two distinct overpay definitions are
preserved and LABELLED distinctly: (a) same-day, same-country cheapest-rival;
(b) country × month best-of-your-own-suppliers. They will not reconcile — that
is correct."* Its acceptance is *"Both figures appear with their grain in the
label; no surface implies they should match."* Hence `GRAIN_SAME_DAY` /
`GRAIN_COUNTRY_MONTH` ride the results themselves, and the two euro fields are
deliberately named DIFFERENTLY (`avoidable_eur` vs `benchmark_gap_eur`) so no
consumer can add them together by writing the obvious loop.

R53 — WHY THIS MODULE READS NOTHING LIKE `contract_audit`
-----------------------------------------------------------
R53, verbatim: *"Legal framing per analysis must not be flattened: contract
breach = 'money the supplier owes' (claim letter); same-day overpay =
'negotiation evidence, NOT a contractual claim-back' (printed on every sheet);
peer/excise/estimate = 'indicative, verify'."*

`contract_audit`/`overcharge`/`overcharge_pack` own the FIRST framing and are
allowed to say "owes": a contract term was breached, the gap is a contractual
entitlement, and WO-83 renders it as a formal PDF letter with a 30-day
credit/refund demand. **This module owns the SECOND framing and is not allowed
to say any of that.** An overpay figure is not a debt: nobody agreed that this
supplier would match the cheapest rival on the day, so there is no term to
breach and nothing is owed. Presenting it as a claim would be a false assertion
to a counterparty, on our client's letterhead.

The separation is STRUCTURAL, not editorial, in four ways a test asserts:

1. **Different framing constant.** `LEGAL_FRAMING` here is R53's own words for
   this analysis and shares no clause with `contract_audit.LEGAL_FRAMING`.
2. **Different vocabulary, down to the field names.** Nothing this module emits
   — no dataclass field, no schema field — contains `recover`, `owed`, `owes`,
   `claim`, `demand`, `due` or `debt`. The euro is `avoidable_eur` (§2.5's own
   adjective, "**Avoidable** overpay"), never `recover_eur`.
3. **No shared code path with the claim-back family.** This module imports
   neither `contract_audit` nor `overcharge` nor `overcharge_pack`, and none of
   them imports it. There is therefore no expression through which a figure
   computed here could reach `overcharge.detected_eur` — the euro WO-83's demand
   letter prints — even by a future accident.
4. **No lifecycle, no artifact, no write verb.** `overcharge` exists because a
   contractual claim needs a state machine and a letter. This surface has
   neither: three read functions, three GETs, nothing persisted.

§4.19 states the same rule from the platform side and it is honoured literally:
these analyses gate nothing, mutate nothing and persist nothing. A test compares
every `fuel_transactions` row column-by-column before and after a run.

THE PRICE BASIS — STATED, AS §3.G G1 REQUIRES
----------------------------------------------
**NET EUR/L, final — VAT excluded, rebates applied.** §3.G G1, verbatim:
*"Prices everywhere are NET EUR/L, FINAL — VAT excluded, rebates applied. This
basis must be stated on any new report surface. → NET/effective price =
`net_eur_eff / qty`."* R49 adds *"every comparison/ordering uses the effective
price"* — which is why every €/L in this module is `net_eur_eff / qty` and never
`net_eur / qty`. The as-invoiced price appears only inside the expected-rebate
analysis, where the DIFFERENCE between the two IS the subject (R49's *"both …
exposed so the rebate value is visible"*).

The basis makes WO-84 load-bearing here: before the off-invoice rebate merge,
`net_eur_eff` was an exact copy of `net_eur` on every stored row, so a supplier
that pays its rebate on a separate invoice (§4.2's canonical Q8/Port One case)
would have looked expensive on every surface in this module. That is why
G4.7 depends on G4.2 in the roadmap, and it is a real dependency, not sequencing.

§4.14 / §4.15 — THE GATE THIS ORDER EXISTS TO GET RIGHT
---------------------------------------------------------
A same-day price comparison spanning countries and document currencies is the
single most likely place for an illegitimate cross-currency comparison to creep
in, so two separate rules are enforced rather than assumed.

**Like is compared with like.** Every comparison partitions on `country` before
any arithmetic (§2.5: *"the cheapest same-day, **same-country** rival"*), and on
`product_group == "Diesel"` through `queries.price_comparison_transactions`. A
€/L is only ever compared against another €/L of the same product in the same
country on the same day.

**The EUR basis is checked, not assumed.** `contract_audit`'s docstring argues
that a EUR figure exists for every stored row because ingestion refuses a line it
cannot convert — true of `statement_ingest`, the production path, but NOT
structurally true of the row writer itself: `fuel_ingest.ingest_transaction`
takes `fx_source` as an optional argument defaulting to `None` and never checks
it against `net_eur`, and `FuelTransaction.fx_source` is nullable with
`"unknown"` among its legal values (`app/models/fx.py`: *"unknown → no rate
available → the EUR figure is NULL, never a guessed number"*). So this module
checks, per row:

* `fx_source == "unknown"` — the writer positively asserted that no rate was
  available. Whatever is in `net_eur_eff` is not a converted euro.
* a NON-EUR document currency with `fx_source IS NULL` — a foreign amount with
  no recorded conversion at all, which is exactly §4.14's *"it never labels a
  foreign amount EUR"*.

A EUR-currency row with a NULL `fx_source` PASSES: EUR is the identity and
involves no rate, which is also why every EUR fixture and every EUR production
line keeps working unchanged.

**And it REFUSES rather than excluding.** `recovery.py` excludes a
currency-mismatch claim and reports the exclusion as a count, which is right
there: a claim is a self-contained object and dropping it distorts nothing else.
A comparison set is not that. Silently dropping one supplier's line from a day
changes the *cheapest rival* for every OTHER supplier of that day — it can turn a
real overpay into a zero, or a zero into an overpay. There is no honest
"excluded: 1" footnote for a comparison, so the analysis refuses with the
tree's existing `fx_rate_unavailable` code and names what is wrong. Fails CLOSED.

§4.9 — DECIMAL, AND WHERE THE ROUNDING HAPPENS
------------------------------------------------
Every price is an exact `Decimal` quotient and **every comparison is made on the
unrounded quotients**, so a line sitting exactly on a boundary can never flip
because of a presentation rounding (`contract_audit`'s discipline, and the
reason an exact tie provably yields no finding). Then, once:

    delta_eur_l   = q(supplier_price − rival_price, 0.0001)   # display, 4 dp
    avoidable_eur = q2(exact_delta × litres)                  # the euro, 1× q2

The euro is quantized from the EXACT delta, never from the already-rounded
display figure, so it never inherits two roundings. Litres are never quantized
(§4.2 row 9 / §4.9's own €/L-denominator carve-out).

DOCUMENTED INTERPRETATIONS (stated, never silently assumed)
-------------------------------------------------------------
The spec states the formulas and leaves three things unstated. Each is decided
here in the open, the way `contract_audit` states its most-specific-wins term
tie-break and `rebate` states its pro-rata-by-litres allocation:

1. **Both overpay grains are restricted to Diesel.** §2.5 row 1 says "diesel
   only" verbatim; row 2 states its grain as country × month and is silent on
   product. Comparing one supplier's blended €/L across Diesel + AdBlue + Toll
   against another supplier's differently-mixed blend measures PRODUCT MIX, not
   price, and would manufacture a gap out of mix alone. A wrong money figure is
   the exact class of error this family exists to prevent, so the narrowing is
   applied and recorded rather than left to chance.
2. **A supplier's price for a grain cell is VOLUME-WEIGHTED**, `Σ net_eur_eff /
   Σ qty` over its lines in that cell. §2.5's formula is `litres × (this
   supplier's eff €/L − …)`, which needs exactly ONE €/L per supplier per cell;
   the volume-weighted mean is the only aggregate under which `litres ×` and the
   underlying line euros reconcile. (§2.5 says elsewhere, about the margin
   report's pack average, that a simple mean *"let a 50 L fill move the pack as
   much as a 40,000 L fill"* — the same reasoning, stated by the spec itself.)
3. **`expected_rebate_eur = typical €/L × litres`.** §2.5 states the flag but no
   euro. This is the arithmetic product of two harvested figures, reported as an
   advisory magnitude so an operator can rank which missing rebate to chase
   first. It is not a claim, it is named so it cannot be mistaken for one, and
   there is no code path from it to `overcharge.detected_eur`.

WHAT THIS MODULE DELIBERATELY DOES NOT CONTAIN
------------------------------------------------
§2.5 lists nine analyses. This module implements the three named above,
completely. The other six are follow-up slices of board G4.7 with a stated
blocker each, recorded in `docs/plan/plan-a/wo/WO-87-overpay-benchmark.md`:
the **peer benchmark** (R55's antitrust gate) needs a cross-entity cohort policy
decision; the **margin report** needs the `my_prices`/`wholesale_prices` tables
of §3.H H5; **supplier reliability** needs an append-only `advertised_prices`
table; **anomalies** (R54) are six rules and an order of their own; and the **FX
markup trend** needs a market-wide markup series. None is stubbed here (§10).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionError, ValidationError
from app.core.money import q, q2
from app.models.transport.fuel_transaction import FuelTransaction
from app.services import modules
from app.services.transport import queries

# §3.G G1 / R49: "this basis must be stated on any new report surface".
PRICE_BASIS = "NET EUR/L, final — VAT excluded, rebates applied"

# R53's SECOND framing, in R53's own words. It is NOT a paraphrase and it is not
# a softened version of the claim-back framing — see the module docstring.
LEGAL_FRAMING = "Negotiation evidence, NOT a contractual claim-back"

# R52 — "both figures appear with their GRAIN in the label". These are R52's own
# two descriptions, so the two numbers can never be presented as reconcilable.
GRAIN_SAME_DAY = "same-day, same-country cheapest rival"
GRAIN_COUNTRY_MONTH = "country × month, best-of-your-own-suppliers"

# §2.5 "Expected rebate", verbatim: flags a line where the rebate is "missing
# (abs(rebate) < 0.005)". A €/L threshold, deliberately the same magnitude as
# `contract_audit.TOLERANCE` and deliberately a separate constant: they answer
# different questions (is a rebate ABSENT vs is a contracted rebate SHORT) and
# tying them together would make one analysis's tuning silently retune the other.
REBATE_MISSING_TOLERANCE = Decimal("0.005")

# €/L display precision — `contract_audit.RATE_EXP`, restated rather than
# imported: importing it would create the code path between the two framings
# that R53 exists to prevent (module docstring, point 3).
RATE_EXP = Decimal("0.0001")

# The currency every figure in this module is expressed in, stated rather than
# assumed (§4.14).
CURRENCY = "EUR"

# The `fx_source` provenance value meaning "no rate was available, so the EUR
# figure is not a converted euro" — `app/models/fx.py` / ADR-0010's
# platform-wide enum. Restated as a literal rather than IMPORTED because a
# transport service may only import `app.models.transport` and `app.models.base`
# (ADR-0023 rule 2, asserted by `tests/test_boundaries.py::
# test_transport_services_do_not_import_other_domain_models`). `rebate.py`
# states its own two `fx_source` values the same way, for the same reason, and
# `tests/transport/test_wo87_savings.py` pins this string against the real enum
# so the two cannot drift apart.
FX_SOURCE_UNKNOWN = "unknown"

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


# --------------------------------------------------------------------------- #
# Result shapes — every euro a Decimal, every field name free of claim-back
# vocabulary (R53; asserted by tests/transport/test_wo87_r53_framing.py)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SameDayOverpay:
    """One (country × day × supplier) cell that paid more than the cheapest
    RIVAL network available to it that day. Basis: NET EUR/L, final.

    `litres` is the supplier's own volume in the cell — the multiplier §2.5
    states — and `suppliers_that_day` is carried so a reader can see the
    comparison was real (it is never below 2).
    """

    country: str
    txn_date: date
    supplier: str
    litres: Decimal
    eur_l_eff: Decimal
    cheapest_rival_supplier: str
    cheapest_rival_eur_l_eff: Decimal
    delta_eur_l: Decimal
    avoidable_eur: Decimal
    suppliers_that_day: int
    lines: int


@dataclass(frozen=True)
class SupplierOverpayTotal:
    """§2.5's attribution, rolled up: *"attributed to the country of supply and
    the supplier that charged the premium"*."""

    country: str
    supplier: str
    litres: Decimal
    avoidable_eur: Decimal
    days: int


@dataclass(frozen=True)
class SameDayOverpayResult:
    """R52 grain (a). `avoidable_eur` is NOT comparable with — and must never be
    added to — `InternalBenchmarkResult.benchmark_gap_eur`: different grain,
    different denominator, and the spec says so (*"They will not reconcile —
    that is correct"*). `grain` rides the result so no surface can present one
    without saying which it is."""

    period: str
    country: str | None
    currency: str
    price_basis: str
    legal_framing: str
    grain: str
    product_group: str
    findings: tuple[SameDayOverpay, ...]
    by_supplier: tuple[SupplierOverpayTotal, ...]
    avoidable_eur: Decimal
    lines_compared: int
    days_compared: int
    days_without_a_rival: int
    lines_skipped_zero_qty: int


@dataclass(frozen=True)
class InternalBenchmarkRow:
    """One (country × supplier) cell of the month against the BEST of your own
    suppliers in that country — including itself, which is why the best supplier
    appears with a zero gap rather than being hidden: it is the supplier the
    volume would be routed TO, so it belongs on the sheet."""

    country: str
    supplier: str
    litres: Decimal
    eur_l_eff: Decimal
    best_supplier: str
    best_eur_l_eff: Decimal
    gap_eur_l: Decimal
    benchmark_gap_eur: Decimal
    lines: int


@dataclass(frozen=True)
class InternalBenchmarkResult:
    """R52 grain (b) — *"money you could have saved by routing volume to the
    cheaper supplier you were ALREADY using"*. Self-sourced: every price in it
    is one this org itself paid, which is the reason §2.5 records that this
    grain carries **no antitrust exposure** while the peer benchmark does."""

    period: str
    country: str | None
    currency: str
    price_basis: str
    legal_framing: str
    grain: str
    product_group: str
    rows: tuple[InternalBenchmarkRow, ...]
    benchmark_gap_eur: Decimal
    countries_compared: int
    suppliers_compared: int
    lines_compared: int
    lines_skipped_zero_qty: int


@dataclass(frozen=True)
class LearnedRebate:
    """What a (supplier, country) pair's rebate USUALLY looks like, learned from
    all history. `learned_from_lines` is the sample it was learned from — shown
    because an expectation formed from two lines deserves less trust than one
    formed from two hundred, and this module states that rather than encoding a
    minimum it was not given (see `expected_rebate`)."""

    supplier: str
    country: str
    typical_eur_l: Decimal
    learned_from_lines: int


@dataclass(frozen=True)
class MissingRebateLine:
    """One line of the period whose pair HAS a learned rebate and which carries
    none. Both prices are exposed (R49) precisely because their difference is
    the subject: `applied_eur_l` is what this line actually got."""

    fuel_transaction_id: str
    entity_id: str
    supplier: str
    country: str
    station: str
    txn_date: date
    litres: Decimal
    eur_l_doc: Decimal
    eur_l_eff: Decimal
    applied_eur_l: Decimal
    typical_eur_l: Decimal
    expected_rebate_eur: Decimal
    learned_from_lines: int


@dataclass(frozen=True)
class ExpectedRebateResult:
    """§2.5's "Expected rebate" in full — the half WO-84 deliberately left to
    this board. WO-84 harvested the per-(supplier, country) EXISTENCE
    expectation (`rebate.missing_source_warnings`: this pair had a rebate
    document before and has none now); this is the per-LINE one (this pair's
    rebate is usually €X/L and THIS line got none)."""

    period: str
    supplier: str | None
    currency: str
    price_basis: str
    legal_framing: str
    tolerance_eur_l: Decimal
    expectations: tuple[LearnedRebate, ...]
    findings: tuple[MissingRebateLine, ...]
    expected_rebate_eur: Decimal
    lines_examined: int
    lines_with_a_rebate: int
    lines_without_an_expectation: int
    lines_skipped_zero_qty: int


# --------------------------------------------------------------------------- #
# Gates — module entitlement, input shape, and the EUR-basis check
# --------------------------------------------------------------------------- #


async def _require_module(db: AsyncSession, org_id: str) -> None:
    """`modules.is_enabled`, never `require_enabled` — a service signals with
    `AppError`, not `fastapi.HTTPException` (ADR-P3 rule 3). Fails CLOSED."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


def validate_period(period: str) -> None:
    """`"YYYY-MM"` — `FuelTransaction.period`'s own shape, and the same refusal
    code `contract_audit` and `rebate` raise. Fails CLOSED for the reason those
    modules give: silently analysing an empty set for a typo'd month looks
    exactly like "you never overpaid anyone", which is a materially different
    and far more dangerous answer than "that is not a period"."""
    if not _PERIOD_RE.match(period):
        raise ValidationError(
            f"'{period}' is not a valid period — use YYYY-MM", code="invalid_period"
        )


def _validate_country(country: str) -> str:
    up = (country or "").upper()
    if not _COUNTRY_RE.match(up):
        raise ValidationError(
            f"'{country}' is not an ISO 3166-1 alpha-2 country", code="invalid_country"
        )
    return up


def _require_eur_basis(rows: list[FuelTransaction]) -> None:
    """§4.14/§4.15 — refuse rather than guess, and refuse rather than quietly
    shrink the comparison set (module docstring).

    Two conditions make a row's EUR figure untrustworthy, and only two:

    * `fx_source == "unknown"` — the writer positively recorded that no rate was
      available (`app/models/fx.py`). Whatever `net_eur_eff` holds is not a
      converted euro.
    * a non-EUR document currency with NO recorded provenance at all
      (`fx_source IS NULL`) — §4.14's *"it never labels a foreign amount EUR"*.

    A EUR-currency row with a NULL `fx_source` is fine: EUR is the identity and
    no rate is involved. That is deliberate and it is what keeps every EUR line
    ever written by `fuel_ingest` (whose `fx_source` argument defaults to
    `None`) inside the analysis.

    The message names the currencies and the count rather than a row id: an
    operator fixes this by refreshing the ECB rates and re-ingesting a
    STATEMENT, not by editing one line.
    """
    bad: dict[str, int] = defaultdict(int)
    for r in rows:
        currency = (r.currency or "").upper()
        source = r.fx_source
        if source == FX_SOURCE_UNKNOWN or (currency != CURRENCY and source is None):
            bad[currency or "?"] += 1
    if not bad:
        return
    detail = ", ".join(f"{cur} ({n} line{'s' if n != 1 else ''})" for cur, n in sorted(bad.items()))
    raise ValidationError(
        f"{sum(bad.values())} line(s) in scope have no established EUR basis — {detail}. "
        "A price comparison is refused rather than run at a guessed rate, and rather "
        "than run over a silently smaller set (dropping one supplier's line changes "
        "the cheapest rival for every other supplier that day).",
        code="fx_rate_unavailable",
    )


# --------------------------------------------------------------------------- #
# Shared arithmetic — READ-ONLY from here down
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Cell:
    """One supplier's aggregated position inside a grain cell: total litres,
    total effective net, and the EXACT unrounded €/L quotient the comparisons
    are made on. Never quantized here — quantization is presentation and it
    happens once, at the finding."""

    supplier: str
    litres: Decimal
    net_eur_eff: Decimal
    lines: int

    @property
    def eur_l_eff(self) -> Decimal:
        return self.net_eur_eff / self.litres


def _aggregate(rows: list[FuelTransaction]) -> dict[str, _Cell]:
    """Volume-weighted per-supplier aggregation of one grain cell (documented
    interpretation 2). Callers have already excluded zero-litre lines, so
    `litres` is always positive and `eur_l_eff` is always defined."""
    litres: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    nets: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    lines: dict[str, int] = defaultdict(int)
    for r in rows:
        litres[r.supplier] += Decimal(r.qty)
        nets[r.supplier] += Decimal(r.net_eur_eff)
        lines[r.supplier] += 1
    return {
        s: _Cell(supplier=s, litres=litres[s], net_eur_eff=nets[s], lines=lines[s]) for s in litres
    }


def _priced_rows(rows: list[FuelTransaction]) -> tuple[list[FuelTransaction], int]:
    """(the lines a €/L is defined for, how many were skipped).

    §4.2 row 9 is explicit that quantity *"can be 0 (promo correction lines)"*.
    A €/L is undefined for such a line and it cannot overpay per litre, so it is
    SKIPPED and counted — never treated as a €0.00 price, which would make it
    the cheapest "rival" of every day it appears in and suppress every real
    finding in that country.
    """
    priced = [r for r in rows if Decimal(r.qty) > 0]
    return priced, len(rows) - len(priced)


# --------------------------------------------------------------------------- #
# 1. Avoidable overpay (same-day) — R52 grain (a)
# --------------------------------------------------------------------------- #


async def same_day_overpay(
    db: AsyncSession,
    org_id: str,
    *,
    period: str,
    country: str | None = None,
) -> SameDayOverpayResult:
    """§2.5's *"Avoidable overpay (same-day)"*, complete.

    `litres × (this supplier's eff €/L − the cheapest same-day, same-country
    RIVAL's eff €/L)`, diesel only, requiring ≥2 suppliers that day, positive
    deltas only, attributed to the country of supply and the supplier that
    charged the premium.

    Basis: **NET EUR/L, final — VAT excluded, rebates applied** (§3.G G1 / R49).
    Framing: **negotiation evidence, NOT a contractual claim-back** (R53). This
    figure gates nothing, mutates nothing and is not money anyone owes.

    Gate order, fails CLOSED: module entitlement → `period` shape → `country`
    shape → the §4.15 EUR-basis check over the whole comparison set.

    "RIVAL" excludes the supplier itself, which matters at both ends of the
    day's price list: the cheapest supplier compares against the SECOND
    cheapest and so drops out on the sign test (its delta is negative), while
    everyone else compares against the cheapest. An exact tie yields a delta of
    zero, and *"positive deltas only"* means zero is not a finding — the
    comparison is made on the unrounded quotients so that boundary cannot move.

    A day with fewer than two suppliers produces NOTHING and is counted in
    `days_without_a_rival`. There is no rival, so there is no evidence — never a
    false positive, and never a silent zero that reads as "you were competitive".
    """
    await _require_module(db, org_id)
    validate_period(period)
    scope_country = _validate_country(country) if country is not None else None

    rows = list(
        await db.scalars(
            queries.price_comparison_transactions(org_id, period=period, country=scope_country)
        )
    )
    _require_eur_basis(rows)
    priced, skipped = _priced_rows(rows)

    by_day: dict[tuple[str, date], list[FuelTransaction]] = defaultdict(list)
    for r in priced:
        by_day[(r.country, r.txn_date)].append(r)

    findings: list[SameDayOverpay] = []
    days_compared = 0
    days_without_a_rival = 0
    for (ctry, day), day_rows in sorted(by_day.items()):
        cells = _aggregate(day_rows)
        if len(cells) < 2:
            days_without_a_rival += 1
            continue
        days_compared += 1
        for cell in sorted(cells.values(), key=lambda c: c.supplier):
            rival = min(
                (c for c in cells.values() if c.supplier != cell.supplier),
                key=lambda c: (c.eur_l_eff, c.supplier),
            )
            delta = cell.eur_l_eff - rival.eur_l_eff
            if delta <= 0:
                continue
            avoidable = q2(delta * cell.litres)
            if avoidable <= 0:
                # A positive delta so small it rounds to nothing is not evidence
                # of anything; reporting €0.00 would clutter the sheet an
                # operator takes into a negotiation.
                continue
            findings.append(
                SameDayOverpay(
                    country=ctry,
                    txn_date=day,
                    supplier=cell.supplier,
                    litres=cell.litres,
                    eur_l_eff=q(cell.eur_l_eff, RATE_EXP),
                    cheapest_rival_supplier=rival.supplier,
                    cheapest_rival_eur_l_eff=q(rival.eur_l_eff, RATE_EXP),
                    delta_eur_l=q(delta, RATE_EXP),
                    avoidable_eur=avoidable,
                    suppliers_that_day=len(cells),
                    lines=cell.lines,
                )
            )

    return SameDayOverpayResult(
        period=period,
        country=scope_country,
        currency=CURRENCY,
        price_basis=PRICE_BASIS,
        legal_framing=LEGAL_FRAMING,
        grain=GRAIN_SAME_DAY,
        product_group=queries.DIESEL,
        findings=tuple(findings),
        by_supplier=_roll_up(findings),
        avoidable_eur=q2(sum((f.avoidable_eur for f in findings), Decimal("0"))),
        lines_compared=len(priced),
        days_compared=days_compared,
        days_without_a_rival=days_without_a_rival,
        lines_skipped_zero_qty=skipped,
    )


def _roll_up(findings: list[SameDayOverpay]) -> tuple[SupplierOverpayTotal, ...]:
    """§2.5's attribution — per (country of supply, supplier that charged the
    premium). Rolled up from the SAME findings the caller sees, so the total on
    the summary and the total on the detail can never disagree (the
    one-source rule `overcharge_pack` states for its two artifacts)."""
    litres: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    euros: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    days: dict[tuple[str, str], set[date]] = defaultdict(set)
    for f in findings:
        key = (f.country, f.supplier)
        litres[key] += f.litres
        euros[key] += f.avoidable_eur
        days[key].add(f.txn_date)
    return tuple(
        SupplierOverpayTotal(
            country=ctry,
            supplier=sup,
            litres=litres[(ctry, sup)],
            avoidable_eur=q2(euros[(ctry, sup)]),
            days=len(days[(ctry, sup)]),
        )
        for ctry, sup in sorted(litres)
    )


# --------------------------------------------------------------------------- #
# 2. Internal benchmark — R52 grain (b)
# --------------------------------------------------------------------------- #


async def internal_benchmark(
    db: AsyncSession,
    org_id: str,
    *,
    period: str,
    country: str | None = None,
) -> InternalBenchmarkResult:
    """§2.5's *"Internal benchmark"*, complete.

    `Σ_supplier qty × (supplier_eff − best_eff)` per **country × month** —
    *"money you could have saved by routing volume to the cheaper supplier you
    were ALREADY using."*

    Basis: **NET EUR/L, final** (§3.G G1 / R49). Framing: **negotiation
    evidence, NOT a contractual claim-back** (R53).

    THE DIFFERENCE FROM `same_day_overpay`, WHICH IS THE WHOLE POINT OF R52:
    `best_eff` here is the best of ALL the country's suppliers **including the
    one being measured** (so the best supplier's own gap is exactly zero), and
    the cell is a whole MONTH rather than a day. `same_day_overpay` compares
    against the best of the OTHERS on ONE day. The two figures are therefore
    computed over different denominators and *"will not reconcile — that is
    correct"* (R52). Nothing in this module adds them, and their euro fields are
    named differently so nothing downstream can either.

    Self-sourced by construction: every price compared here is one this org
    itself paid, which is why §2.5 records this grain as carrying no antitrust
    exposure — unlike the peer benchmark, which is a separate slice behind
    R55's `PEER_MIN_CONTRIBUTORS` gate and is deliberately not built here.

    Every supplier of a compared country gets a row, including the best one at a
    zero gap: it is the supplier the volume would be routed TO, so hiding it
    would leave the sheet unable to answer its own question. A country with one
    supplier is reported with its single zero-gap row — there is nothing to route
    to, and the row says so honestly rather than vanishing.
    """
    await _require_module(db, org_id)
    validate_period(period)
    scope_country = _validate_country(country) if country is not None else None

    rows = list(
        await db.scalars(
            queries.price_comparison_transactions(org_id, period=period, country=scope_country)
        )
    )
    _require_eur_basis(rows)
    priced, skipped = _priced_rows(rows)

    by_country: dict[str, list[FuelTransaction]] = defaultdict(list)
    for r in priced:
        by_country[r.country].append(r)

    out: list[InternalBenchmarkRow] = []
    suppliers = 0
    for ctry, country_rows in sorted(by_country.items()):
        cells = _aggregate(country_rows)
        suppliers += len(cells)
        best = min(cells.values(), key=lambda c: (c.eur_l_eff, c.supplier))
        for cell in sorted(cells.values(), key=lambda c: c.supplier):
            gap = cell.eur_l_eff - best.eur_l_eff
            out.append(
                InternalBenchmarkRow(
                    country=ctry,
                    supplier=cell.supplier,
                    litres=cell.litres,
                    eur_l_eff=q(cell.eur_l_eff, RATE_EXP),
                    best_supplier=best.supplier,
                    best_eur_l_eff=q(best.eur_l_eff, RATE_EXP),
                    gap_eur_l=q(gap, RATE_EXP),
                    benchmark_gap_eur=q2(gap * cell.litres),
                    lines=cell.lines,
                )
            )

    return InternalBenchmarkResult(
        period=period,
        country=scope_country,
        currency=CURRENCY,
        price_basis=PRICE_BASIS,
        legal_framing=LEGAL_FRAMING,
        grain=GRAIN_COUNTRY_MONTH,
        product_group=queries.DIESEL,
        rows=tuple(out),
        benchmark_gap_eur=q2(sum((r.benchmark_gap_eur for r in out), Decimal("0"))),
        countries_compared=len(by_country),
        suppliers_compared=suppliers,
        lines_compared=len(priced),
        lines_skipped_zero_qty=skipped,
    )


# --------------------------------------------------------------------------- #
# 3. Expected rebate — §2.5's learning loop, the half WO-84 left to this board
# --------------------------------------------------------------------------- #


def _median(values: list[Decimal]) -> Decimal:
    """The exact-Decimal median. A MEDIAN, not a mean, for the reason §2.5 gives
    for every other learned bound in this family — *"every bound is learned from
    the data's own spread"* and the robust form is the one that survives one
    freak line. The even case averages the two middle values and stays exact
    until the caller quantizes."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


async def expected_rebate(
    db: AsyncSession,
    org_id: str,
    *,
    period: str,
    supplier: str | None = None,
) -> ExpectedRebateResult:
    """§2.5's *"Expected rebate"*, complete: *"Learns the typical €/L rebate per
    (supplier, country) from all history; flags a line where it is missing
    (`abs(rebate) < 0.005`). Rationale: catches rebates that arrive on a
    separate invoice we often don't see (the Q8/Port One case)."*

    THE APPLIED REBATE IS AN IDENTITY, NOT AN ESTIMATE. §4.2's two-tier model
    says an on-invoice discount is *"already inside `net_eur`"* and an
    off-invoice rebate *"lands ONLY in `net_eur_eff`"*, so their per-litre
    difference IS the rebate that reached this line:

        applied_eur_l = (net_eur − net_eur_eff) / qty

    WHAT "FROM ALL HISTORY" MEANS HERE. The expectation for a (supplier,
    country) is the MEDIAN applied €/L over every line of that pair, in every
    period this org holds, **that actually carries a rebate** (`abs(applied) >=
    0.005`). Restricting the sample to rebate-bearing lines is what makes the
    flag fireable at all: including the zero lines would drag the "typical"
    toward zero in exactly the situation the analysis exists to detect, and the
    question being answered is *"what does this pair's rebate look like WHEN it
    is present"*.

    NO MINIMUM-SAMPLE CONSTANT IS INVENTED. §2.5 states none, and the tree's
    existing posture is WO-84's `rebate.missing_source_warnings`, which forms
    its expectation from a SINGLE earlier period. This function keeps that
    posture and reports `learned_from_lines` on every expectation and every
    finding instead, so the operator sees how much evidence is behind the flag
    rather than having a threshold chosen for them.

    THE PAIR GRAIN IS `(supplier, country)`, VERBATIM — no product dimension is
    added. A rebate is negotiated per network per country; §2.5 states the grain
    explicitly and adding a third key would change the harvested definition
    rather than implement it. Only lines a €/L is DEFINED for participate
    (`qty > 0`), which is the same skip `same_day_overpay` makes and for the
    same reason.

    Basis: **NET EUR/L, final** (§3.G G1 / R49) — both prices exposed, because
    here their difference is the subject. Framing: **negotiation evidence, NOT a
    contractual claim-back** (R53). ADVISORY (§4.19): a missing rebate is a
    document to go and find, not a debt. Where the missing document IS
    contractually owed, that is `contract_audit`'s `short discount` flag against
    a configured `expected_discount_eur_l` term — a different analysis, with a
    different framing, in a different module, reached by a different route.

    Gate order, fails CLOSED: module entitlement → `period` shape → the §4.15
    EUR-basis check over BOTH the history sample and the period's lines (a
    median learned from an unconvertible euro would be a fabricated
    expectation, so the history is checked exactly as strictly as the period).
    """
    await _require_module(db, org_id)
    validate_period(period)

    # `supplier or None` preserves the `contract_audit.audit` call-site
    # convention exactly: an empty string means "every supplier".
    history = list(await db.scalars(queries.fuel_transactions(org_id, supplier=supplier or None)))
    _require_eur_basis(history)

    samples: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for row in history:
        qty = Decimal(row.qty)
        if qty <= 0:
            continue
        applied = (Decimal(row.net_eur) - Decimal(row.net_eur_eff)) / qty
        if abs(applied) >= REBATE_MISSING_TOLERANCE:
            samples[(row.supplier, row.country)].append(applied)

    learned = {pair: (_median(values), len(values)) for pair, values in samples.items() if values}
    expectations = tuple(
        LearnedRebate(
            supplier=sup,
            country=ctry,
            typical_eur_l=q(learned[(sup, ctry)][0], RATE_EXP),
            learned_from_lines=learned[(sup, ctry)][1],
        )
        for sup, ctry in sorted(learned)
    )

    period_rows = [r for r in history if r.period == period]
    priced, skipped = _priced_rows(period_rows)

    findings: list[MissingRebateLine] = []
    with_rebate = 0
    without_expectation = 0
    for row in sorted(priced, key=lambda r: (r.supplier, r.txn_date, r.line_seq)):
        qty = Decimal(row.qty)
        eur_l_doc = Decimal(row.net_eur) / qty
        eur_l_eff = Decimal(row.net_eur_eff) / qty
        applied = eur_l_doc - eur_l_eff
        if abs(applied) >= REBATE_MISSING_TOLERANCE:
            with_rebate += 1
            continue
        entry = learned.get((row.supplier, row.country))
        if entry is None:
            # No history, so no expectation — silence, not a warning. Crying
            # wolf on every ordinary supplier would train an operator to ignore
            # the one flag that matters (`rebate.missing_source_warnings`'
            # stated posture, applied at line grain).
            without_expectation += 1
            continue
        typical, sample = entry
        findings.append(
            MissingRebateLine(
                fuel_transaction_id=row.id,
                entity_id=row.entity_id,
                supplier=row.supplier,
                country=row.country,
                station=row.station,
                txn_date=row.txn_date,
                litres=qty,
                eur_l_doc=q(eur_l_doc, RATE_EXP),
                eur_l_eff=q(eur_l_eff, RATE_EXP),
                applied_eur_l=q(applied, RATE_EXP),
                typical_eur_l=q(typical, RATE_EXP),
                # Documented interpretation 3 — an advisory magnitude, quantized
                # once from the EXACT learned median (not from its 4 dp display
                # form), never a claim and never `overcharge.detected_eur`.
                expected_rebate_eur=q2(typical * qty),
                learned_from_lines=sample,
            )
        )

    return ExpectedRebateResult(
        period=period,
        supplier=supplier,
        currency=CURRENCY,
        price_basis=PRICE_BASIS,
        legal_framing=LEGAL_FRAMING,
        tolerance_eur_l=REBATE_MISSING_TOLERANCE,
        expectations=expectations,
        findings=tuple(findings),
        expected_rebate_eur=q2(sum((f.expected_rebate_eur for f in findings), Decimal("0"))),
        lines_examined=len(priced),
        lines_with_a_rebate=with_rebate,
        lines_without_an_expectation=without_expectation,
        lines_skipped_zero_qty=skipped,
    )


__all__ = [
    "CURRENCY",
    "FX_SOURCE_UNKNOWN",
    "GRAIN_COUNTRY_MONTH",
    "GRAIN_SAME_DAY",
    "LEGAL_FRAMING",
    "PRICE_BASIS",
    "RATE_EXP",
    "REBATE_MISSING_TOLERANCE",
    "ExpectedRebateResult",
    "InternalBenchmarkResult",
    "InternalBenchmarkRow",
    "LearnedRebate",
    "MissingRebateLine",
    "SameDayOverpay",
    "SameDayOverpayResult",
    "SupplierOverpayTotal",
    "expected_rebate",
    "internal_benchmark",
    "same_day_overpay",
    "validate_period",
]
