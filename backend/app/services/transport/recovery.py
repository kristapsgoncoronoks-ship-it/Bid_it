"""The cash-recovery analytics service (G4.3; R38; `BA_fleet_fuel.md` §2.4).

The first transport surface that answers a question about the PORTFOLIO rather
than about one claim: *"how much money can we still recover this refund year,
and what is stopping each euro of it?"* Every claim of the year lands in exactly
one of the six harvested readiness states, and the north-star euros are totalled
beside them.

WHAT THE HARVESTED SPEC ACTUALLY SAYS
--------------------------------------
`BA_fleet_fuel.md` §2.4, verbatim: *"Six readiness states: `ready · deadline ·
missing · below · submitted · paid`. North-star €: recovered, awaiting,
claimable, overcharges, median days-to-refund. Deadline risk = within 60 days of
30-Sep."* R38 adds the binding constraint and the acceptance line: *"built on the
CANONICAL claims and recovery queries, NEVER a forked query"* / *"the dashboard
totals reconcile exactly with the underlying claim reports."*

NOTHING HERE COMPUTES A FIGURE OF ITS OWN
-------------------------------------------
That is the whole design. Each input comes from the one function this codebase
already trusts for it, so a rule change lands in one place and this surface
cannot drift from the gate that enforces it:

* the claim set          -> `claim.list_claims(..., year=...)`  (the ONE listing query)
* a draft's stage        -> `status.derive_stage`               (D3, the AUTO codes)
* a draft's VAT base     -> `freeze.preview_vat_base`           (the same preview `lock.submit_claim` gates on)
* the Art. 17 threshold  -> `minimum.below_minimum`             (R8, compared in the CORRECT currency)
* the filing time-bar    -> `deadline.deadline_status`          (R9, `DEADLINE_RISK_DAYS = 60`)

`deadline.py`'s own docstring said these functions were "pure functions any
future consumer (a dashboard, G4.3 ...) can call". This is that consumer.

THE BUCKET MAPPING, AND THE FOUR INTERPRETATIONS IN IT
--------------------------------------------------------
The spec NAMES the six states but does not define their precedence, so the
mapping onto this codebase's two harvested lifecycle layers (§3.D: the coarse
ENGINE status, and the system-derived AUTO stage codes) is stated here:

    paid       claim.status == "paid"                       (3A "Money received")
    submitted  claim.status in {"submitted", "approved"}    (2 / 3)
    missing    draft, derive_stage -> "1A"                  (1A "Missing documents")
    below      draft, "1C" AND below_minimum                (A3 / Art. 17)
    deadline   draft, otherwise fileable, deadline_status != "ok"
    ready      every other draft (1B, 1E, a waiver-only 1C) (1E "Ready to submit")

1. **`1B` (period not ended) folds into `ready`.** The spec gives six states and
   no seventh; a `1B` claim has nothing an operator must fix — it becomes
   fileable when its own period closes. Reporting it as `missing` would invent a
   data gap. No seventh bucket is invented (master-context §10).
2. **A waiver-only `1C` is `ready`, not `below`.** `derive_stage` emits `1C` for
   EITHER of two causes (below-minimum, or an active receipt-control waiver —
   see `status.py`'s docstring). Only the first is a THRESHOLD problem, so this
   module re-asks `minimum.below_minimum` through the same canonical preview
   rather than reading the shared code as if it meant one thing.
3. **`deadline` applies only to an otherwise-fileable claim** — the most
   actionable meaning available ("this one is ready and the clock is running").
   A `missing`/`below` claim inside the window keeps its BLOCKING bucket (the
   bucket says what to do) and is still counted in `deadline_risk_claims` (the
   count says how urgent). This is exactly why R38 lists the deadline-risk COUNT
   separately from the six buckets: under any other reading the count would be a
   restatement of one bucket's size.
4. **`withdrawn`/`rejected` match none of the six** and are reported in an
   explicit `excluded` block WITH their reason — never silently dropped.
   `sum(bucket claims) + sum(excluded claims) == total_claims` holds exactly, and
   is asserted by a test. A seventh readiness state would be invented vocabulary;
   a silent drop would make the row count lie.

MONEY: NET EUR, AND WHY §4.14 IS THE HARD CONSTRAINT HERE
-----------------------------------------------------------
Every euro on this surface is `vat_eur` — the column `BA_fleet_fuel.md` calls
*"Reclaimable VAT in EUR — the VAT-refund north star"*. These are MULTI-COUNTRY
claims, so summing anything else would be a §4.14 violation dressed as a total:

* `vat_local` is NEVER summed and never surfaces here (it is per-refund-state).
* `paid_amount` is NEVER summed: `VatRefundClaim.currency` is the LOCAL currency
  frozen from the claim's lines, so `paid_amount` carries no currency of its own
  in the current model (and nothing writes it — see DEVIATIONS). `recovered_eur`
  therefore reads the FROZEN `vat_eur`, which is unambiguously EUR and computed
  over exactly the locked claim set (R13/C10).
* A DRAFT claim whose lines span currencies makes `preview_vat_base` refuse
  (`claim_currency_mismatch`, §4.14). This module catches that PER CLAIM: the
  claim buckets `missing` (it is missing a filable single-currency VAT base — an
  operator must fix the data), contributes ZERO euros, and increments
  `currency_mismatch_claims` so the shortfall is VISIBLE rather than silent. One
  bad claim never blanks a whole year, and no foreign amount is ever labelled
  EUR. Refusing the entire dashboard would be the other defensible choice; it is
  rejected because it would let one malformed draft hide the deadline count that
  the north-star KPI ("deadline misses = 0") depends on.

A draft claim's own `vat_eur` COLUMN is NULL by construction (WO-49: frozen only
at submission), which is why `claimable_eur` goes through `preview_vat_base` and
not the column. Reading the column would report €0 claimable for every unfiled
claim — the single most dangerous error possible on this surface.

`median_days_to_refund` is not money, but it is computed in exact `Decimal`
(quantized to 0.1 through `money.q`) precisely so that no float — via
`statistics.median` or otherwise — can enter this module at all (§4.9).

READ-ONLY, AND FAIL-CLOSED ON THE ENTITLEMENT
-----------------------------------------------
No `db.add`, no model attribute assignment, no `audit.record`, and the route
issues no `db.commit()`. The stage evaluator this calls seeds default checklist
rules idempotently (flushed, never committed) — the same read-only posture
`GET /transport/claims/{id}/checklist` has carried since WO-76. The `transport`
module entitlement is checked INSIDE this service before any query (ADR-P3
rule 3), fails CLOSED, and is not delegated to the route.

Missing data never raises: a year with no claims returns the six empty buckets
and zeroes, not a 404 and not an exception. Only a malformed `year` refuses
(422 `invalid_year`), because silently returning an empty year for a typo would
look exactly like "you have nothing to recover" — a materially different, and
dangerous, answer.

DEVIATIONS — what the spec defines that this data model cannot yet support
---------------------------------------------------------------------------
* **The `overcharges` euro.** §2.4/R38 list it among the north-star figures, but
  it comes from `contract_audit`/`overcharge.py` — board **G4.5 / R41**, which
  does not exist in this codebase. Emitting a zero labelled "overcharges" would
  read as "we found no supplier breaches", a different and false statement, so
  the figure is OMITTED rather than faked (§10). It joins this response when
  G4.5 ships.
* **`median_days_to_refund` has no populated inputs yet.** Nothing writes
  `submitted_date`/`paid_date` on a claim today (`lock.submit_claim` sets
  `status` and `status_code`, not the dates; the engine-state transitions that
  would stamp them are G2.9, decision-gated in `docs/DECISIONS-NEEDED.md` §10).
  The measure is implemented against the harvested definition and reports
  `days_to_refund_sample` beside it, so a `null` median reads as "no claim has
  both dates yet" instead of as a mystery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, PermissionError, ValidationError
from app.core.money import q, q2
from app.models.transport.vat_claim import VatRefundClaim
from app.services import modules
from app.services.transport.claim import list_claims
from app.services.transport.deadline import DEADLINE_RISK_DAYS, deadline_status
from app.services.transport.freeze import preview_vat_base
from app.services.transport.minimum import below_minimum
from app.services.transport.status import derive_stage

# The six harvested readiness states, in `BA_fleet_fuel.md` §2.4's own order.
# The response always carries all six, including the empty ones — a bucket that
# vanished when it hit zero would make "nothing is at deadline risk" and "the
# dashboard forgot to tell you" look identical.
READINESS_STATES: tuple[str, ...] = (
    "ready",
    "deadline",
    "missing",
    "below",
    "submitted",
    "paid",
)

# The engine statuses of `models.transport.vat_claim.CLAIM_STATUSES` that match
# NO readiness state — reported with their reason rather than dropped
# (interpretation 4 in the module docstring). Documentation of what the model
# carries TODAY, not the dispatch rule: `recovery_dashboard` dispatches on the
# statuses it KNOWS and excludes everything else by name, so a status added
# later is excluded (visible, named, counted) rather than silently folded into
# a euro total.
EXCLUDED_STATUSES: tuple[str, ...] = ("withdrawn", "rejected")

# `VatRefundClaim.ref_period` is `"YYYY-..."` — a four-digit year (the
# `claim._PERIOD_RE` shape, backed by the DB CHECK constraint). Outside this
# range no claim can exist at all, so it is the exact, non-arbitrary bound.
MIN_YEAR = 1000
MAX_YEAR = 9999

# One decimal place of days — exact Decimal, never a float median (§4.9).
_TENTH = Decimal("0.1")


@dataclass(frozen=True)
class Bucket:
    """One readiness state: how many claims, and how much reclaimable VAT."""

    state: str
    claims: int
    vat_eur: Decimal


@dataclass(frozen=True)
class Excluded:
    """A claim set matching none of the six readiness states, named."""

    reason: str
    claims: int


@dataclass(frozen=True)
class RecoveryDashboard:
    """The whole surface. `currency` is a constant `"EUR"` because every figure
    here is a `vat_eur` sum — stated on the wire rather than assumed, so a
    consumer can never render a foreign amount with a EUR sign (§4.14)."""

    year: int
    currency: str
    total_claims: int
    buckets: tuple[Bucket, ...]
    excluded: tuple[Excluded, ...]
    recovered_eur: Decimal
    awaiting_eur: Decimal
    claimable_eur: Decimal
    deadline_risk_claims: int
    currency_mismatch_claims: int
    median_days_to_refund: Decimal | None
    days_to_refund_sample: int


def validate_year(year: int) -> None:
    """Refuse a year no `ref_period` could ever carry (422, `invalid_year`).
    Fails CLOSED for the reason the module docstring gives: an empty result for
    a typo'd year is indistinguishable from "you have nothing to recover"."""
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ValidationError(
            f"'{year}' is not a valid refund year — use a four-digit year",
            code="invalid_year",
        )


def _median(values: list[int]) -> Decimal | None:
    """The exact-Decimal median of a day count. Returns None for an empty
    sample — never 0, which would claim refunds arrive the same day they are
    filed. An even sample averages the two middle values through `money.q` at
    one decimal (ROUND_HALF_UP), so no float ever appears (§4.9)."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return q(Decimal(ordered[mid]), _TENTH)
    return q((Decimal(ordered[mid - 1]) + Decimal(ordered[mid])) / 2, _TENTH)


async def _bucket_draft(
    db: AsyncSession,
    org_id: str,
    claim: VatRefundClaim,
    *,
    today: date | None,
) -> tuple[str, Decimal, bool, bool]:
    """Bucket ONE draft claim: `(state, vat_eur, at_deadline_risk,
    currency_mismatch)`.

    Order follows the module docstring's mapping exactly. The VAT base is
    previewed FIRST because it is the one step that can refuse: if the claim's
    lines span currencies, `preview_vat_base` raises `claim_currency_mismatch`
    (§4.14) and there is no single-currency figure to gate on OR to sum — the
    claim buckets `missing` with zero euros and is counted. Doing the preview
    first also means `derive_stage` (which makes the SAME `freeze._sum_lines`
    call internally, for its own 1C caveat check) can no longer hit that path.
    """
    try:
        vat_eur, vat_local, _currency = await preview_vat_base(db, org_id, claim.id)
    except ConflictError as exc:
        if exc.code != "claim_currency_mismatch":
            raise
        return "missing", Decimal("0.00"), False, True

    at_risk = deadline_status(claim.ref_period, today=today) != "ok"

    stage = await derive_stage(db, org_id, claim.id, today=today)
    if stage == "1A":
        return "missing", vat_eur, at_risk, False
    if stage == "1C" and below_minimum(
        country=claim.refund_country,
        ref_period=claim.ref_period,
        vat_eur=vat_eur,
        vat_local=vat_local,
    ):
        return "below", vat_eur, at_risk, False

    # 1B / 1E / a waiver-only 1C — nothing blocks this claim, so the clock is
    # the only thing left that can be wrong with it.
    return ("deadline" if at_risk else "ready"), vat_eur, at_risk, False


async def recovery_dashboard(
    db: AsyncSession,
    org_id: str,
    year: int,
    *,
    today: date | None = None,
) -> RecoveryDashboard:
    """R38 — every claim of the refund `year` bucketed into the six readiness
    states, with the north-star euros beside them. Read-only; see the module
    docstring for every definition, citation and interpretation.

    `today` is a TEST SEAM (defaults to the real date inside
    `deadline.deadline_status`/`status.derive_stage`), mirroring
    `lock.submit_claim`'s own — and, like that one, it is deliberately NOT
    exposed on the wire: a client that could post its own clock could make a
    claim 20 days from a fatal time-bar report as comfortable.

    Gate order, the transport-wide entry-point pattern, fails CLOSED:
    1. the `transport` module entitlement (ADR-P3 rule 3 — never delegated to
       the route, which carries its own structural permission check);
    2. `year` validation (422 `invalid_year`).

    Cost note: bucketing walks the year's claims one at a time because
    `derive_stage`/`preview_vat_base` are the CANONICAL per-claim evaluators and
    R38 forbids forking them into a set-based query. A refund year holds one
    claim per (entity x refund country x period), so the walk is bounded by the
    portfolio's own shape; if it ever stops being, the fix is a batch form of
    those evaluators used by BOTH callers — never a second implementation here.
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    validate_year(year)

    claims = await list_claims(db, org_id, year=year)

    counts: dict[str, int] = dict.fromkeys(READINESS_STATES, 0)
    euros: dict[str, Decimal] = {state: Decimal("0") for state in READINESS_STATES}
    excluded: dict[str, int] = {}
    deadline_risk = 0
    currency_mismatch = 0
    days: list[int] = []

    for claim in claims:
        # Dispatch on the statuses this surface KNOWS, and exclude by name
        # otherwise — never the reverse. A status added to `CLAIM_STATUSES`
        # later (or already stored by a path this module has not met) must not
        # fall through into a euro total: silently counting an unrecognised
        # claim as "awaiting €X" would be a wrong money figure, which is the
        # exact class of error this surface exists to prevent. `EXCLUDED_
        # STATUSES` documents the two the model has today; the branch below
        # catches anything else by the SAME mechanism, under its own name.
        if claim.status == "draft":
            state, vat_eur, at_risk, mismatch = await _bucket_draft(db, org_id, claim, today=today)
            if at_risk:
                deadline_risk += 1
            if mismatch:
                currency_mismatch += 1
        elif claim.status in ("paid", "submitted", "approved"):
            # Filed. The FROZEN figure is the claim's own (R13/C10) — never a
            # re-derivation, which is the whole point of freezing it.
            state = "paid" if claim.status == "paid" else "submitted"
            vat_eur = claim.vat_eur if claim.vat_eur is not None else Decimal("0")
            if state == "paid" and claim.submitted_date and claim.paid_date:
                days.append((claim.paid_date - claim.submitted_date).days)
        else:
            excluded[claim.status] = excluded.get(claim.status, 0) + 1
            continue

        counts[state] += 1
        euros[state] += vat_eur

    draft_states = ("ready", "deadline", "missing", "below")
    return RecoveryDashboard(
        year=year,
        currency="EUR",
        total_claims=len(claims),
        buckets=tuple(
            Bucket(state=s, claims=counts[s], vat_eur=q2(euros[s])) for s in READINESS_STATES
        ),
        excluded=tuple(Excluded(reason=r, claims=n) for r, n in sorted(excluded.items())),
        recovered_eur=q2(euros["paid"]),
        awaiting_eur=q2(euros["submitted"]),
        claimable_eur=q2(sum((euros[s] for s in draft_states), Decimal("0"))),
        deadline_risk_claims=deadline_risk,
        currency_mismatch_claims=currency_mismatch,
        median_days_to_refund=_median(days),
        days_to_refund_sample=len(days),
    )


__all__ = [
    "DEADLINE_RISK_DAYS",
    "EXCLUDED_STATUSES",
    "READINESS_STATES",
    "Bucket",
    "Excluded",
    "RecoveryDashboard",
    "recovery_dashboard",
    "validate_year",
]
