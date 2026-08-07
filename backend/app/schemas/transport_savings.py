"""Wire schemas for the overpay / benchmark analyses (WO-87, G4.7 — R52, R53).

Field-for-field from `app/services/transport/savings.py`'s result dataclasses —
this module shapes the wire and computes nothing (engineering-rules §3). Every
amount and every €/L is typed `Decimal`, so pydantic v2 serializes it as a JSON
STRING and no float ever appears in a money path (§4.9). `litres` is a `Decimal`
too: it is the €/L denominator and a float round-trip of it would move the
prices computed from it.

R52 — THE TWO GRAINS ARE LABELLED ON THE WIRE, NOT ONLY IN THE SERVICE
------------------------------------------------------------------------
`grain` is a required field on both overpay responses and the euro fields are
named differently on purpose (`avoidable_eur` on the same-day grain,
`benchmark_gap_eur` on the country×month one). A client that wanted to add the
two numbers would have to write two different field names to do it, and would
have the spec's own *"they will not reconcile — that is correct"* labelled on
each response while doing so.

R53 — THE FRAMING RIDES EVERY RESPONSE
----------------------------------------
`legal_framing` and `price_basis` are required on all three responses, carrying
`savings.LEGAL_FRAMING` (*"Negotiation evidence, NOT a contractual claim-back"*)
and `savings.PRICE_BASIS` verbatim — the same discipline WO-83 applied to the
claim-back artifacts, which is what makes a framing impossible to flatten by
re-wording it downstream: there is only one string.

**No field name in this module contains `recover`, `owed`, `owes`, `claim`,
`demand`, `due` or `debt`**, and a test asserts it. That is not a style rule: an
overpay figure is not a debt, and a field called `recoverable_eur` on this
surface would be a false assertion the moment a client rendered it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class SameDayOverpayLineOut(BaseModel):
    """One (country × day × supplier) cell that paid above the cheapest RIVAL
    network available that day. Basis: NET EUR/L, final."""

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


class SupplierOverpayTotalOut(BaseModel):
    """§2.5's attribution — the country of supply and the supplier that charged
    the premium."""

    country: str
    supplier: str
    litres: Decimal
    avoidable_eur: Decimal
    days: int


class SameDayOverpayOut(BaseModel):
    """R52 grain (a). `days_without_a_rival` is reported rather than hidden: a
    day with a single supplier yields no finding because there was no rival to
    compare against, which is a materially different fact from "you were
    competitive that day"."""

    period: str
    country: str | None = None
    currency: str
    price_basis: str
    legal_framing: str
    grain: str
    product_group: str
    findings: list[SameDayOverpayLineOut]
    by_supplier: list[SupplierOverpayTotalOut]
    avoidable_eur: Decimal
    lines_compared: int
    days_compared: int
    days_without_a_rival: int
    lines_skipped_zero_qty: int


class InternalBenchmarkRowOut(BaseModel):
    """One (country × supplier) cell of the month against the best of your own
    suppliers in that country — including itself, so the best supplier appears
    with a zero gap. It is the supplier the volume would be routed to."""

    country: str
    supplier: str
    litres: Decimal
    eur_l_eff: Decimal
    best_supplier: str
    best_eur_l_eff: Decimal
    gap_eur_l: Decimal
    benchmark_gap_eur: Decimal
    lines: int


class InternalBenchmarkOut(BaseModel):
    """R52 grain (b) — self-sourced, so every price in it is one this org itself
    paid."""

    period: str
    country: str | None = None
    currency: str
    price_basis: str
    legal_framing: str
    grain: str
    product_group: str
    rows: list[InternalBenchmarkRowOut]
    benchmark_gap_eur: Decimal
    countries_compared: int
    suppliers_compared: int
    lines_compared: int
    lines_skipped_zero_qty: int


class LearnedRebateOut(BaseModel):
    """What a (supplier, country) pair's rebate usually looks like, and how many
    lines that was learned from."""

    supplier: str
    country: str
    typical_eur_l: Decimal
    learned_from_lines: int


class MissingRebateLineOut(BaseModel):
    """One line whose pair has a learned rebate and which carries none. Both
    prices are exposed (R49) because their difference is the subject."""

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


class ExpectedRebateOut(BaseModel):
    """§2.5's "Expected rebate" — the per-LINE half of the analysis WO-84 took
    only the per-(supplier, country) existence half of."""

    period: str
    supplier: str | None = None
    currency: str
    price_basis: str
    legal_framing: str
    tolerance_eur_l: Decimal
    expectations: list[LearnedRebateOut]
    findings: list[MissingRebateLineOut]
    expected_rebate_eur: Decimal
    lines_examined: int
    lines_with_a_rebate: int
    lines_without_an_expectation: int
    lines_skipped_zero_qty: int
