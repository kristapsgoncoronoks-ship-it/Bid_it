"""Transport routes slice 8 — the diesel excise-duty refund (WO-91, G4.6).

Thin controllers over `app.services.transport.excise`: parse -> structural
permission gate -> call the already-gated service -> shape the response
(engineering-rules §3). Every refusal a client can see (`module_not_enabled`,
`invalid_period`, `invalid_country`, `excise_country_not_supported`,
`invalid_excise_rate`, `no_excise_findings`) is raised BY THE SERVICE as an
`app.core.errors.AppError` and rendered by the one `app.main` handler as
`{"detail", "code"}` — this module maps nothing, so the wire vocabulary cannot
drift from the service layer (master-context §4.20).

AUTHORIZATION (ADR-0024, structural)
--------------------------------------
Router-level **`TRANSPORT_READ`** — the permission whose own definition in
`app/core/authz.py` has named this surface since it was introduced:
`TRANSPORT_READ = "transport.read"  # fuel/toll analytics, excise (advisory)`.
WO-79 recorded the reservation, WO-81 and WO-87 claimed it for their read
surfaces, and this order claims the half it was actually named for. **No
permission member is added** (master-context §10).

The two rate WRITES declare the stricter **`VAT_WRITE`** per-route — the same
existing permission `overcharges.py` uses for contract-term configuration
(`_WRITE`). Configuring an excise rate is exactly that kind of act: it is
admin-curated master data that multiplies real litres into a figure a haulier
files with a customs authority. No new permission is invented for it.

THE CAVEATS ARE PART OF THE RESPONSE, NOT PART OF THE UI
-----------------------------------------------------------
R42's acceptance line is *"The UI shows the indicative-rate and eligibility
caveats on every surface that shows the number"*. This module satisfies it by
making that impossible to get wrong: `eligibility`, `rate_caveat`,
`legal_framing` and `eligibility_asserted` are REQUIRED fields on every response
carrying a euro or a rate, sourced from the service's own single constants. A
client cannot receive the number without them.

READ-ONLY EXCEPT THE RATE: the report and the customs packet perform no write of
any kind (§4.19 / §3.L — an advisory seam must be *"structurally incapable of
gating or mutating a legal figure"*), and `db.commit()` appears only on the two
rate mutations.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.transport_excise import (
    ExciseCellOut,
    ExciseRateIn,
    ExciseRateOut,
    ExciseRatesOut,
    ExciseReportOut,
    SkippedCountryOut,
)
from app.services.transport import excise as excise_svc

# Structural authorization (ADR-0024): router-level TRANSPORT_READ; the two rate
# mutations declare the stricter VAT_WRITE per-route below.
router = APIRouter(
    prefix="/transport",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.TRANSPORT_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.VAT_WRITE))]


def _rates_out(resolved: dict[str, Decimal], overridden: set[str]) -> ExciseRatesOut:
    """One place that shapes the rate view, so the list route and the two write
    routes cannot describe the same state differently."""
    return ExciseRatesOut(
        countries=list(excise_svc.REFUND_COUNTRIES),
        default_rate_eur_per_1000l=excise_svc.DEFAULT_RATE_EUR_PER_1000L,
        rates=[
            ExciseRateOut(
                country=country,
                rate_eur_per_1000l=rate,
                is_override=country in overridden,
                rate_caveat=excise_svc.RATE_CAVEAT,
            )
            for country, rate in resolved.items()
        ],
        eligibility=excise_svc.ELIGIBILITY_STATEMENT,
        eligibility_asserted=False,
        rate_caveat=excise_svc.RATE_CAVEAT,
        legal_framing=excise_svc.LEGAL_FRAMING,
        currency=excise_svc.CURRENCY,
    )


async def _current_rates(db: AsyncSession, org_id: str) -> ExciseRatesOut:
    resolved = await excise_svc.list_rates(db, org_id)
    rows = await excise_svc.list_rate_rows(db, org_id)
    return _rates_out(resolved, {r.country for r in rows})


@router.get("/excise", response_model=ExciseReportOut)
async def get_excise_report(
    current: CurrentUser,
    db: DbSession,
    period: str = Query(description="YYYY-MM — the accounting month"),
    entity_id: str | None = Query(default=None, description="claimant legal entity scope filter"),
    country: str | None = Query(
        default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2 scope filter"
    ),
):
    """R42's diesel excise-duty refund: the period's validated **diesel** litres
    per **(entity × country)**, at `litres × rate/1,000 L`.

    **This figure asserts NO eligibility.** The conditions that qualify a
    haulier — vehicle weight (>= 7.5 t) and carrier registration — are
    deliberately not modelled by this product (`BA_fleet_fuel.md` §3.L, §9.2
    item 14). Read it as excise that may be reclaimable IF you qualify, never as
    excise you are owed. `eligibility` carries the full statement and
    `eligibility_asserted` is always `false`.

    Rates are **indicative defaults**: EUR 30.00 per 1,000 litres is an explicit
    placeholder in a reported EUR 25-33 band, the duty is not harmonised and
    national rates are revised, so `rate_caveat` rides the response and
    `rate_is_override` says per row whether a human verified the rate.
    Framing: **indicative / advisory — verify before relying** (R53). Filed with
    **customs** (`filed_with`) — a separate regime from the VAT refund.

    Every figure crosses the wire as an exact decimal STRING (§4.9), litres
    included. A country this product holds no rate for produces no row and is
    reported in `skipped_countries` with its litres — never a EUR 0.00 line,
    because "we hold no rate for this state" and "this state refunds you
    nothing" are different facts.

    A month with no qualifying litres is a 200 with no rows and a zero total,
    never a 404. A malformed period refuses 422 `invalid_period`.
    """
    result = await excise_svc.excise_report(
        db, current.org_id, period=period, entity_id=entity_id, country=country
    )
    return ExciseReportOut(
        period=result.period,
        entity_id=result.entity_id,
        country=result.country,
        currency=result.currency,
        product_group=result.product_group,
        litre_basis=result.litre_basis,
        legal_framing=result.legal_framing,
        eligibility=result.eligibility,
        eligibility_asserted=result.eligibility_asserted,
        rate_caveat=result.rate_caveat,
        filed_with=result.filed_with,
        rows=[
            ExciseCellOut(
                entity_id=c.entity_id,
                entity_name=c.entity_name,
                country=c.country,
                litres=c.litres,
                rate_eur_per_1000l=c.rate_eur_per_1000l,
                rate_is_override=c.rate_is_override,
                indicative_excise_eur=c.indicative_excise_eur,
                lines=c.lines,
            )
            for c in result.rows
        ],
        skipped_countries=[
            SkippedCountryOut(country=s.country, litres=s.litres, lines=s.lines)
            for s in result.skipped_countries
        ],
        litres=result.litres,
        indicative_excise_eur=result.indicative_excise_eur,
        lines_examined=result.lines_examined,
    )


@router.get("/excise/rates", response_model=ExciseRatesOut)
async def list_excise_rates(current: CurrentUser, db: DbSession):
    """The rate every state's figure will actually be computed at.

    Returns all seven states `BA_fleet_fuel.md` §2.4 records as operating a
    commercial-diesel excise refund, each with its RESOLVED rate — the
    organization's typed override where one exists (`is_override: true`), and
    the harvested indicative default of EUR 30.00 per 1,000 litres everywhere
    else. The default is an **explicit placeholder** in a reported EUR 25-33
    band (§2.4, Appendix B); `rate_caveat` says so on every row.

    A state absent from `countries` has no rate at all, which is not the same as
    a rate of zero: the figure simply reports nothing for it.
    """
    return await _current_rates(db, current.org_id)


@router.put("/excise/rates", response_model=ExciseRatesOut, dependencies=_WRITE)
async def set_excise_rate(current: CurrentUser, db: DbSession, body: ExciseRateIn):
    """Type the rate a customs authority actually applies for one state,
    replacing the harvested placeholder for it. `VAT_WRITE`.

    Idempotent: re-sending the same rate is a no-op that audits nothing. Any
    change audits old->new in the same transaction (§4.16) — this rate
    multiplies real litres into a figure a haulier files with customs.

    Refuses, both fail-CLOSED and both the service's own: 422
    `excise_country_not_supported` for a state this product does not record as
    operating the regime (storing a rate there would assert one exists), and 422
    `invalid_excise_rate` for a non-positive rate (a state that refunds nothing
    is expressed by holding no rate for it, never by a zero).
    """
    await excise_svc.set_rate(
        db,
        current.org_id,
        country=body.country,
        rate_eur_per_1000l=body.rate_eur_per_1000l,
    )
    await db.commit()
    return await _current_rates(db, current.org_id)


@router.delete("/excise/rates", response_model=ExciseRatesOut, dependencies=_WRITE)
async def remove_excise_rate(
    current: CurrentUser,
    db: DbSession,
    country: str = Query(min_length=2, max_length=2, description="ISO 3166-1 alpha-2"),
):
    """Drop the typed override so the state falls back to the harvested
    indicative default. `VAT_WRITE`.

    This is *"we no longer hold a verified rate"*, not *"this state refunds
    nothing"* — the figure goes back to being explicitly placeholder-based, and
    `is_override` flips to `false`. A state with no override is a no-op, never a
    404: there is nothing to hide and nothing to guess at.
    """
    await excise_svc.remove_rate(db, current.org_id, country=country)
    await db.commit()
    return await _current_rates(db, current.org_id)
