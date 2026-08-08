"""Wire schemas for the diesel excise-duty refund (WO-91, G4.6 — R42, R53).

Field-for-field from `app/services/transport/excise.py` — this module shapes the
wire and computes nothing (engineering-rules §3). Every rate and every euro is
typed `Decimal`, so pydantic v2 serializes it as a JSON STRING and no float ever
appears in a money path (§4.9). `litres` is a `Decimal` too: it is the figure's
multiplicand and a float round-trip of a 3-decimal litre total would move the
euro computed from it.

THE ELIGIBILITY LIMITATION RIDES THE WIRE, IT IS NOT A UI DECORATION
-----------------------------------------------------------------------
R42's acceptance line is *"The UI shows the indicative-rate and eligibility
caveats on **every surface that shows the number**"*, and `BA_fleet_fuel.md`
§3.L is the rule behind it: *"`excise.py` | **Asserts NO eligibility** (vehicle
>=7.5t / carrier registration not modelled); rates are indicative defaults."*

So `eligibility`, `rate_caveat`, `legal_framing` and `eligibility_asserted` are
**required** fields on every response that carries a euro. They are not optional
metadata a client may drop: a surface physically cannot render the number
without having received the denial next to it, and `eligibility_asserted` is a
literal `False` so the denial survives a UI that truncates prose. The strings
come from `excise.ELIGIBILITY_STATEMENT` / `excise.RATE_CAVEAT` /
`excise.LEGAL_FRAMING` — one definition each, in the service, so no wording can
be softened downstream.

**No field name in this module contains `recover`, `owed`, `owes`, `claim`,
`demand`, `due`, `debt` or `payable`**, and
`tests/transport/test_wo91_excise_eligibility.py` asserts it against the SAME
word list WO-87 wrote for the overpay surface. That is not a style rule: a
`recoverable_eur` field here would read as an entitlement the moment a client
rendered it, and whether this haulier is entitled to anything is precisely what
this product does not model. The euro is `indicative_excise_eur` — the
qualification lives inside the field name, where a renderer cannot lose it.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ExciseRateOut(BaseModel):
    """One state's rate as the figure will actually use it: the org's typed
    override where there is one, the harvested indicative default otherwise.

    `is_override` is what distinguishes *"an operator verified this with
    customs"* from *"this is the spec's EUR 30.00 placeholder"* — the whole
    reason §9.2 item 13 exists as an open question. A surface that shows the
    rate without it would be presenting a placeholder as a statutory figure.
    """

    country: str
    rate_eur_per_1000l: Decimal
    is_override: bool
    rate_caveat: str


class ExciseRatesOut(BaseModel):
    """Every state this product records as operating the regime, with its
    resolved rate. Carries the caveats so the rate table is itself one of R42's
    *"surfaces that show the number"*."""

    countries: list[str]
    default_rate_eur_per_1000l: Decimal
    rates: list[ExciseRateOut]
    eligibility: str
    eligibility_asserted: bool
    rate_caveat: str
    legal_framing: str
    currency: str


class ExciseCellOut(BaseModel):
    """One (entity × country) cell — R42's grain. `litres` is a `Decimal`
    because it is the figure's multiplicand: a float round-trip of a
    three-decimal litre total would move the euro computed from it.

    `rate_is_override` distinguishes a rate a human verified with customs from
    the harvested EUR 30.00 placeholder. A packet that could not tell them apart
    would present a placeholder as a statutory figure.
    """

    entity_id: str
    entity_name: str
    country: str
    litres: Decimal
    rate_eur_per_1000l: Decimal
    rate_is_override: bool
    indicative_excise_eur: Decimal
    lines: int


class SkippedCountryOut(BaseModel):
    """A country with validated diesel litres in scope for which this product
    holds no rate. It carries litres and a line count and **no euro** — there is
    no figure, which is not the same as a figure of zero."""

    country: str
    litres: Decimal
    lines: int


class ExciseReportOut(BaseModel):
    """The diesel excise-duty refund over one accounting month.

    Every caveat field is REQUIRED — see the module docstring. `filed_with`
    names the addressee because §2.4/§1.2/§6.1 all stress that this is a
    SEPARATE regime from the VAT refund, claimed from customs; a surface that
    lost that would invite the figure onto a VAT filing.
    """

    period: str
    entity_id: str | None
    country: str | None
    currency: str
    product_group: str
    litre_basis: str
    legal_framing: str
    eligibility: str
    eligibility_asserted: bool
    rate_caveat: str
    filed_with: str
    rows: list[ExciseCellOut]
    skipped_countries: list[SkippedCountryOut]
    litres: Decimal
    indicative_excise_eur: Decimal
    lines_examined: int


class ExciseRateIn(BaseModel):
    """Type the rate a customs authority actually applies for one state.

    The country and the positive-rate rule are enforced in the SERVICE
    (`excise_country_not_supported` / `invalid_excise_rate`) as well as bounded
    here — schemas catch shape, services catch business rules (master-context
    DoD 3). `gt=0` is the shape half; the service's refusal explains WHY a zero
    is not "this state refunds nothing".
    """

    country: str = Field(min_length=2, max_length=2)
    rate_eur_per_1000l: Decimal = Field(gt=0)
