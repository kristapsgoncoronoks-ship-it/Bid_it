"""Response schemas for the cash-recovery analytics route (WO-81, G4.3/R38).

Field-for-field from `app/services/transport/recovery.py`'s dataclasses — this
module shapes the wire and computes nothing (engineering-rules §3). Every amount
is typed `Decimal`, so pydantic v2 serializes it as a JSON STRING and no float
ever appears in a money path (master-context §4.9); the service already
quantized each one through `app.core.money.q2`.

`currency` is carried explicitly, always `"EUR"`. It is not decoration: these are
MULTI-COUNTRY claims and every figure here is a `vat_eur` sum, so the response
states the basis rather than leaving a consumer to assume it (§4.14). No
`vat_local` and no `paid_amount` crosses this wire — see the service docstring
for why summing either would be a cross-currency total in disguise.

`median_days_to_refund` is `Decimal | None`, not money: it is a day count kept in
exact Decimal purely so the median of an even sample cannot arrive via a float.
`null` means "no paid claim carries both a submitted and a paid date", which
`days_to_refund_sample` makes legible instead of mysterious.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class RecoveryBucketOut(BaseModel):
    """One of the six harvested readiness states (`BA_fleet_fuel.md` §2.4:
    `ready · deadline · missing · below · submitted · paid`). All six are always
    present, including the empty ones — a bucket that vanished at zero would
    make "nothing is at deadline risk" and "the dashboard forgot" identical."""

    state: str
    claims: int
    vat_eur: Decimal


class RecoveryExcludedOut(BaseModel):
    """Claims matching none of the six readiness states, named by their engine
    status (`withdrawn`/`rejected`). Reported rather than dropped so
    `Σ buckets + Σ excluded == total_claims` holds exactly."""

    reason: str
    claims: int


class RecoveryDashboardOut(BaseModel):
    year: int
    # Always "EUR" — the basis of every figure below, stated not assumed.
    currency: str
    total_claims: int
    buckets: list[RecoveryBucketOut]
    excluded: list[RecoveryExcludedOut]

    # The north-star euros (R38). `claimable_eur` covers the four DRAFT buckets;
    # the per-bucket figures above show how much of it is blocked, and by what.
    recovered_eur: Decimal
    awaiting_eur: Decimal
    claimable_eur: Decimal

    # The SECOND cash stream (G4.5/R41, WO-82): booked cash recovered from
    # suppliers for contract breaches — §2.4's *"recovered_total() = the
    # booked-cash north star"*, NOT the €-exposure detected. Deliberately
    # OUTSIDE the recovered+awaiting+claimable reconciliation above, which is
    # about the year's VAT; folding a second stream into it would make that
    # identity false. WO-81 omitted this field rather than emit a misleading
    # zero; the hole is now closed.
    overcharges_eur: Decimal

    # Counted across every unfiled claim regardless of its bucket — the "how
    # urgent" reading that sits beside the buckets' "what to do" (R38 lists the
    # deadline-risk count separately from the six states for exactly this).
    deadline_risk_claims: int
    # Drafts whose lines span currencies: excluded from every euro above and
    # counted here, so the shortfall is visible rather than silent (§4.14).
    currency_mismatch_claims: int

    median_days_to_refund: Decimal | None = None
    days_to_refund_sample: int
