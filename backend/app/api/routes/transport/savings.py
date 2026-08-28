"""Transport routes slice 7 — the overpay / benchmark analyses (WO-87, G4.7).

Three thin controllers over `app.services.transport.savings`: parse →
structural permission gate → call the already-gated service → shape the response
(engineering-rules §3). Every refusal a client can see (`module_not_enabled`,
`invalid_period`, `invalid_country`, `fx_rate_unavailable`) is raised BY THE
SERVICE as an `app.core.errors.AppError` and rendered by the one `app.main`
handler as `{"detail", "code"}` — this module maps nothing, so the wire
vocabulary cannot drift from the service layer (master-context §4.20).

AUTHORIZATION (ADR-0024, structural)
--------------------------------------
Router-level **`TRANSPORT_READ`** and nothing else. WO-79 recorded the
reservation this honours: *"`TRANSPORT_READ` stays reserved for the derived
analytics/excise slices"*, and WO-81 was the first to claim it. This module is
the same kind of surface — it returns no claim, no lock, no object a client can
act on, only aggregates over rows the `VAT_READ` routes already serve in detail.
**No permission member is added** (§10).

THERE IS NO WRITE VERB HERE, AND THAT IS THE POINT (R53)
-----------------------------------------------------------
`overcharges.py` carries `VAT_WRITE` overrides because a contractual claim-back
has a lifecycle to advance and a demand letter to issue. This surface has
neither, deliberately: the figures it serves are **negotiation evidence, NOT a
contractual claim-back** (R53's second framing, carried on every response as
`legal_framing`), and there is nothing to open, freeze, package or send. A test
asserts the absence — `tests/transport/test_wo87_r53_framing.py::
test_wo87_the_savings_route_module_exposes_no_write_verb` — because "we simply
did not add one" is a fact about today's file, while an asserted absence is a
fact about tomorrow's.

For the same reason this module imports nothing from the overcharge family and
is imported by none of it: there is no expression on this surface through which
an overpay euro could reach the euro a 30-day payment demand quotes.

READ-ONLY BY CONSTRUCTION: three GETs, no `db.commit()`, and nothing to commit —
the service performs no write of any kind (§4.19).

THE PRICE BASIS AND THE GRAIN TRAVEL WITH THE NUMBER
------------------------------------------------------
Every response carries `price_basis` (§3.G G1 / R49: *"this basis must be stated
on any new report surface"*) and, on both overpay grains, `grain` (R52: *"both
figures appear with their grain in the label; no surface implies they should
match"*). They come off the service's own constants, so a consumer renders the
same string the service computed the number under.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.transport_savings import (
    AnomalyOut,
    AnomalyReportOut,
    ExpectedRebateOut,
    InternalBenchmarkOut,
    InternalBenchmarkRowOut,
    LearnedRebateOut,
    MissingRebateLineOut,
    SameDayOverpayLineOut,
    SameDayOverpayOut,
    SupplierOverpayTotalOut,
    SuppressedRuleOut,
)
from app.services.transport import anomaly as anomaly_svc
from app.services.transport import savings as savings_svc

# Structural authorization (ADR-0024): router-level TRANSPORT_READ. Every route
# here is a read, so there is no stricter per-route override — and there is no
# write route to need one.
router = APIRouter(
    prefix="/transport/savings",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.TRANSPORT_READ))],
)


@router.get("/same-day", response_model=SameDayOverpayOut)
async def get_same_day_overpay(
    current: CurrentUser,
    db: DbSession,
    period: str = Query(description="YYYY-MM — the accounting month"),
    country: str | None = Query(
        default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2 scope filter"
    ),
):
    """§2.5's *"Avoidable overpay (same-day)"*: `litres × (this supplier's
    effective €/L − the cheapest same-day, same-country RIVAL's effective
    €/L)`, diesel only, requiring at least two suppliers that day, positive
    deltas only, attributed to the country of supply and the supplier that
    charged the premium.

    Basis: **NET EUR/L, final — VAT excluded, rebates applied**; every figure
    crosses the wire as an exact decimal STRING (§4.9). Framing: **negotiation
    evidence, NOT a contractual claim-back** — this is not money the supplier
    owes, and nothing here gates or changes any claim (R53, §4.19).

    A month with no comparable day is a 200 with no findings and a zero total,
    never a 404: `days_without_a_rival` says why. A line in scope whose EUR
    basis is not established refuses with 422 `fx_rate_unavailable` rather than
    comparing at a guessed rate or over a silently smaller set (§4.15).

    There is deliberately no `supplier` parameter: filtering the ROWS would
    change who the cheapest rival was, so the same query would answer a
    different question. `by_supplier` gives the per-supplier attribution off the
    complete comparison set instead.
    """
    result = await savings_svc.same_day_overpay(db, current.org_id, period=period, country=country)
    return SameDayOverpayOut(
        period=result.period,
        country=result.country,
        currency=result.currency,
        price_basis=result.price_basis,
        legal_framing=result.legal_framing,
        grain=result.grain,
        product_group=result.product_group,
        findings=[
            SameDayOverpayLineOut(
                country=f.country,
                txn_date=f.txn_date,
                supplier=f.supplier,
                litres=f.litres,
                eur_l_eff=f.eur_l_eff,
                cheapest_rival_supplier=f.cheapest_rival_supplier,
                cheapest_rival_eur_l_eff=f.cheapest_rival_eur_l_eff,
                delta_eur_l=f.delta_eur_l,
                avoidable_eur=f.avoidable_eur,
                suppliers_that_day=f.suppliers_that_day,
                lines=f.lines,
            )
            for f in result.findings
        ],
        by_supplier=[
            SupplierOverpayTotalOut(
                country=t.country,
                supplier=t.supplier,
                litres=t.litres,
                avoidable_eur=t.avoidable_eur,
                days=t.days,
            )
            for t in result.by_supplier
        ],
        avoidable_eur=result.avoidable_eur,
        lines_compared=result.lines_compared,
        days_compared=result.days_compared,
        days_without_a_rival=result.days_without_a_rival,
        lines_skipped_zero_qty=result.lines_skipped_zero_qty,
    )


@router.get("/internal-benchmark", response_model=InternalBenchmarkOut)
async def get_internal_benchmark(
    current: CurrentUser,
    db: DbSession,
    period: str = Query(description="YYYY-MM — the accounting month"),
    country: str | None = Query(
        default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2 scope filter"
    ),
):
    """§2.5's *"Internal benchmark"*: `Σ_supplier litres × (supplier effective
    €/L − the best effective €/L in that country)` per **country × month** —
    *"money you could have saved by routing volume to the cheaper supplier you
    were ALREADY using."*

    This is R52's SECOND overpay grain and it will **not** reconcile with
    `/same-day` — different denominator, different comparison, and the spec says
    *"that is correct"*. Both responses carry `grain` so neither can be
    presented as the other, and the euro fields are named differently
    (`benchmark_gap_eur` here, `avoidable_eur` there) so they cannot be summed
    by accident.

    Self-sourced: every price compared is one this organization itself paid,
    which is why this grain carries no antitrust exposure. The PEER benchmark —
    the one that does, and that R55 gates behind a minimum contributor count —
    is a separate slice and is deliberately not served here.

    Basis: **NET EUR/L, final**. Framing: **negotiation evidence, NOT a
    contractual claim-back** (R53).
    """
    result = await savings_svc.internal_benchmark(
        db, current.org_id, period=period, country=country
    )
    return InternalBenchmarkOut(
        period=result.period,
        country=result.country,
        currency=result.currency,
        price_basis=result.price_basis,
        legal_framing=result.legal_framing,
        grain=result.grain,
        product_group=result.product_group,
        rows=[
            InternalBenchmarkRowOut(
                country=r.country,
                supplier=r.supplier,
                litres=r.litres,
                eur_l_eff=r.eur_l_eff,
                best_supplier=r.best_supplier,
                best_eur_l_eff=r.best_eur_l_eff,
                gap_eur_l=r.gap_eur_l,
                benchmark_gap_eur=r.benchmark_gap_eur,
                lines=r.lines,
            )
            for r in result.rows
        ],
        benchmark_gap_eur=result.benchmark_gap_eur,
        countries_compared=result.countries_compared,
        suppliers_compared=result.suppliers_compared,
        lines_compared=result.lines_compared,
        lines_skipped_zero_qty=result.lines_skipped_zero_qty,
    )


@router.get("/expected-rebate", response_model=ExpectedRebateOut)
async def get_expected_rebate(
    current: CurrentUser,
    db: DbSession,
    period: str = Query(description="YYYY-MM — the accounting month"),
    supplier: str | None = Query(default=None, description="fuel-card / toll-network code"),
):
    """§2.5's *"Expected rebate"*: the typical €/L rebate per (supplier,
    country) learned from all history, with the lines of `period` that carry
    none flagged.

    This is the per-LINE half of the analysis. The per-(supplier, country)
    EXISTENCE half already ships as `contract_audit`'s `source_warnings` (WO-84,
    R50) and answers a coarser question — *did the rebate document arrive at
    all this month?* This answers *does THIS line look like the rebate this pair
    usually grants?*, which is how a rebate that arrives on a separate invoice
    we often do not see (the canonical Q8/Port One case) is caught line by line.

    A pair with no rebate-bearing history produces NO finding — with no history
    there is no expectation, and `lines_without_an_expectation` reports how many
    lines that was rather than warning about each one.

    ADVISORY (§4.19). A missing rebate here is a document to go and find, not a
    debt: where the rebate is contractually owed, that is `contract_audit`'s
    `short discount` flag against a configured term, a different analysis with a
    different framing reached by a different route.

    Basis: **NET EUR/L, final** — both the as-invoiced and the effective price
    are exposed on every finding, because here their difference is the subject
    (R49).
    """
    result = await savings_svc.expected_rebate(db, current.org_id, period=period, supplier=supplier)
    return ExpectedRebateOut(
        period=result.period,
        supplier=result.supplier,
        currency=result.currency,
        price_basis=result.price_basis,
        legal_framing=result.legal_framing,
        tolerance_eur_l=result.tolerance_eur_l,
        expectations=[
            LearnedRebateOut(
                supplier=e.supplier,
                country=e.country,
                typical_eur_l=e.typical_eur_l,
                learned_from_lines=e.learned_from_lines,
            )
            for e in result.expectations
        ],
        findings=[
            MissingRebateLineOut(
                fuel_transaction_id=f.fuel_transaction_id,
                entity_id=f.entity_id,
                supplier=f.supplier,
                country=f.country,
                station=f.station,
                txn_date=f.txn_date,
                litres=f.litres,
                eur_l_doc=f.eur_l_doc,
                eur_l_eff=f.eur_l_eff,
                applied_eur_l=f.applied_eur_l,
                typical_eur_l=f.typical_eur_l,
                expected_rebate_eur=f.expected_rebate_eur,
                learned_from_lines=f.learned_from_lines,
            )
            for f in result.findings
        ],
        expected_rebate_eur=result.expected_rebate_eur,
        lines_examined=result.lines_examined,
        lines_with_a_rebate=result.lines_with_a_rebate,
        lines_without_an_expectation=result.lines_without_an_expectation,
        lines_skipped_zero_qty=result.lines_skipped_zero_qty,
    )


@router.get("/anomalies", response_model=AnomalyReportOut)
async def get_anomalies(
    current: CurrentUser,
    db: DbSession,
    period: str = Query(description="YYYY-MM — the accounting month"),
    country: str | None = Query(
        default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2 scope filter"
    ),
):
    """§2.5 row 7 (R54): six rules over one month, every bound learned from the
    data's own spread — `station_price`, `price_divergence`, `volume_spike`,
    `vehicle_price`, `off_period`, `off_hours`.

    There is deliberately no `rule` parameter. All six run or none do: a caller
    that could ask for two would get a quiet answer that reads as "nothing
    unusual happened", which is a stronger claim than two rules can support —
    the reason this was reserved as one order rather than six slices.

    `suppressed` is part of the contract, not a diagnostic. A rule that could
    not run (no prior month to measure a move against, say) is not a rule that
    found nothing, and collapsing the two would hand the operator the
    reassuring reading of an absence of evidence."""
    report = await anomaly_svc.detect(db, current.org_id, period=period, country=country)
    return AnomalyReportOut(
        period=report.period,
        anomalies=[
            AnomalyOut(
                rule=a.rule,
                subject=a.subject,
                country=a.country,
                observed=a.observed,
                expected=a.expected,
                deviation=a.deviation,
                litres=a.litres,
                detail=a.detail,
                line_seq=a.line_seq,
                txn_date=a.txn_date,
            )
            for a in report.anomalies
        ],
        suppressed=[SuppressedRuleOut(rule=r, reason=why) for r, why in report.suppressed],
        count=report.count,
        rules=list(anomaly_svc.RULES),
    )
