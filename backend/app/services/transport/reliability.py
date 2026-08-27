"""Supplier reliability — EVIDENCE about your own captured data (WO-Q).

Owner decision 2026-08-08 §12, recorded in `docs/DECISIONS-NEEDED.md`: *"every
supplier carries a reliability rating, computed from multiple criteria"* —
three of them named: **overcharges**, **exchange-rate treatment**, and **lines
charged that were never agreed** — presented *"so it reads as evidence rather
than a verdict on a counterparty."* The design pass that gate required is
`docs/design/supplier-reliability-rating.md`; this module is its deliverable 2
and follows it without deviation.

THE GOVERNING CONSTRAINT, AND HOW IT IS ENFORCED RATHER THAN PROMISED
----------------------------------------------------------------------
"Evidence, not a verdict" is a property that decays the moment someone adds a
field called `risk_score` or a sentence that sounds like an accusation. So it
is structural here, exactly as R53's framing is in `savings.py`:

- **`FRAMING` is rendered verbatim** off every result. This module owns the
  words; no caller may re-word them.
- **The vocabulary is asserted, not intended.** No field name, band value or
  route path here carries claim vocabulary — `tests/transport/test_wo_q_reliability.py`
  scans this module against WO-87's own `CLAIM_WORDS` (imported, never
  re-typed) with a seeded-violation self-test, and asserts in BOTH directions
  that no reliability figure can reach `overcharge.detected_eur`.
- **Bands are labels about DATA, never about a company**: `clean` / `findings`
  / `recurring`. Not "good", not "risky", not "trustworthy".
- **The rule is rendered beside the label.** Every criterion returns the
  threshold that produced its band, so a reader always sees "recurring" and
  "≥ 3 cases in 12 months" together. A hidden threshold is a verdict wearing a
  label's clothes.
- **The overall rating is the WORST criterion band, never a weighted score.**
  A composite number invites reading precision into a judgement call; "worst of
  three" is explainable in one sentence and cannot be tuned into a ranking.

WHAT IS DERIVED, AND WHY NOTHING IS STORED
--------------------------------------------
Everything except the thresholds. Each criterion is computed on read from rows
that already exist, so a rating can never disagree with the evidence a reader
clicks through to, a corrected row (a rebate merge, a reinstated case) moves
the rating by itself, and there is no snapshot to go stale. The G4.7-era note
that reliability *"needs an append-only `advertised_prices` table"* predates
the owner's criteria and is dropped by the design: advertised-price tracking is
a different, fourth criterion nobody asked for.

An IGNORED claim-back still counts (design §4.1). The §12 ignore action is an
operator choosing not to REACT; the overcharge still happened. Excluding them
would let the rating be managed by ignoring, which is precisely the silent dead
end §12 was decided to prevent — so ignored cases appear in the outcome split
with their audited reasons one click away, and they count.

WHY A THIN SAMPLE GETS NO RATING
----------------------------------
A supplier with under `MIN_ACTIVE_MONTHS` months of activity in the window is
reported `insufficient history` with its month count and NO bands at all. Three
lines in one month is not a pattern, and a `clean` label on it would be a
clean bill nobody earned — the same false comfort in the other direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionError, ValidationError
from app.core.money import q2
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.overcharge import VatOverchargeClaim
from app.models.transport.reliability_threshold import VatReliabilityThreshold
from app.services import audit as audit_svc
from app.services import modules
from app.services.transport import contract_audit, queries

#: Rendered VERBATIM on every result. The reader must always be told what this
#: page is before they read a band. Shares no clause with `savings.LEGAL_FRAMING`
#: or `contract_audit.LEGAL_FRAMING` — a different analysis needs its own words.
FRAMING = (
    "Computed from this workspace's own captured statements and contract terms "
    "over the stated window. It describes patterns in the data — it is not an "
    "assessment of the counterparty."
)

#: The window, per the design. Rolling, recomputed on every read.
WINDOW_MONTHS = 12
#: Below this many months of activity IN the window, no band is published.
MIN_ACTIVE_MONTHS = 3

#: The three band values. Deliberately about the data, never about a company.
BAND_CLEAN = "clean"
BAND_FINDINGS = "findings"
BAND_RECURRING = "recurring"
BAND_INSUFFICIENT = "insufficient_history"
#: Worst-first — `_worst` picks the first band present, which is why the order
#: of this tuple IS the overall rule.
BAND_ORDER = (BAND_RECURRING, BAND_FINDINGS, BAND_CLEAN)

CRITERION_OVERCHARGES = "overcharges"
CRITERION_FX = "exchange_rate_treatment"
CRITERION_UNGOVERNED = "lines_never_agreed"
CRITERIA = (CRITERION_OVERCHARGES, CRITERION_FX, CRITERION_UNGOVERNED)

#: The stated defaults. An org with no threshold row behaves exactly as these
#: describe, and the numbers are rendered next to every band they produce.
DEFAULT_OVERCHARGE_CASES = 3
DEFAULT_OVERCHARGE_EUR_PER_1000 = Decimal("5.00")
DEFAULT_FX_MARKUP_BPS = 50
DEFAULT_UNGOVERNED_SHARE_PCT = Decimal("10.00")

#: `fuel_transactions.fx_source` values this module reasons about.
FX_EUR = "eur"
FX_STATED = "stated"
FX_ECB = "ecb"

_BPS = Decimal("10000")
_PCT_EXP = Decimal("0.01")


@dataclass(frozen=True)
class Thresholds:
    """The org's band boundaries — its row, or the documented defaults."""

    overcharge_cases: int
    overcharge_eur_per_1000: Decimal
    fx_markup_bps: int
    ungoverned_share_pct: Decimal
    #: True when no row exists and these are the code defaults. The screen says
    #: so, because "the platform's default" and "a number you chose" carry very
    #: different weight when a reader is looking at a band.
    is_default: bool


DEFAULT_THRESHOLDS = Thresholds(
    overcharge_cases=DEFAULT_OVERCHARGE_CASES,
    overcharge_eur_per_1000=DEFAULT_OVERCHARGE_EUR_PER_1000,
    fx_markup_bps=DEFAULT_FX_MARKUP_BPS,
    ungoverned_share_pct=DEFAULT_UNGOVERNED_SHARE_PCT,
    is_default=True,
)


@dataclass(frozen=True)
class Criterion:
    """One criterion's band, the figures behind it, and the rule that produced
    it. `rule` is rendered beside `band` — never one without the other."""

    key: str
    band: str
    rule: str
    #: Free-form, criterion-specific counts and euros. Every value is either an
    #: int or a `Decimal` the caller renders as-is; no figure is pre-formatted.
    figures: dict


@dataclass(frozen=True)
class SupplierReliability:
    supplier: str
    #: `insufficient_history` when the sample is too thin — then `criteria` is
    #: empty, because a band nobody earned is worse than no band.
    overall: str
    active_months: int
    net_spend_eur: Decimal
    criteria: tuple[Criterion, ...]


@dataclass(frozen=True)
class ReliabilityReport:
    window_from: str
    window_to: str
    framing: str
    thresholds: Thresholds
    suppliers: tuple[SupplierReliability, ...]


async def _require_module(db: AsyncSession, org_id: str) -> None:
    """`modules.is_enabled`, never `require_enabled` — a service signals with
    `AppError`, not `fastapi.HTTPException` (ADR-P3 rule 3). Fails CLOSED."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


def window_months(as_of: date) -> list[str]:
    """The `WINDOW_MONTHS` accounting months ending at `as_of`'s month,
    oldest first. Period arithmetic lives here so no caller re-derives it."""
    months: list[str] = []
    y, m = as_of.year, as_of.month
    for _ in range(WINDOW_MONTHS):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(months))


def _band(findings: int, over_threshold: bool) -> str:
    if findings == 0:
        return BAND_CLEAN
    return BAND_RECURRING if over_threshold else BAND_FINDINGS


def _worst(bands: list[str]) -> str:
    for candidate in BAND_ORDER:
        if candidate in bands:
            return candidate
    return BAND_CLEAN


def _median(values: list[Decimal]) -> Decimal | None:
    """The middle value, or the mean of the two middle ones. Returns None for
    an empty sample rather than a zero — "no measurement" and "a markup of
    zero" are different facts and the screen renders them differently."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


async def get_thresholds(db: AsyncSession, org_id: str) -> Thresholds:
    row = await db.scalar(
        select(VatReliabilityThreshold).where(VatReliabilityThreshold.org_id == org_id)
    )
    if row is None:
        return DEFAULT_THRESHOLDS
    return Thresholds(
        overcharge_cases=row.overcharge_cases,
        overcharge_eur_per_1000=Decimal(row.overcharge_eur_per_1000),
        fx_markup_bps=row.fx_markup_bps,
        ungoverned_share_pct=Decimal(row.ungoverned_share_pct),
        is_default=False,
    )


async def set_thresholds(
    db: AsyncSession,
    org_id: str,
    *,
    overcharge_cases: int,
    overcharge_eur_per_1000: Decimal,
    fx_markup_bps: int,
    ungoverned_share_pct: Decimal,
) -> Thresholds:
    """Upsert the org's thresholds, audited old→new. Validation mirrors the
    table's CHECKs so the refusal is a 422 with a sentence rather than a
    driver error — the constraints stay as defense in depth."""
    if overcharge_cases < 1:
        raise ValidationError(
            "The overcharge case threshold must be at least 1 — a zero would label "
            "every supplier 'recurring', which inverts the meaning of the band.",
            code="invalid_threshold",
        )
    if overcharge_eur_per_1000 <= 0 or fx_markup_bps <= 0:
        raise ValidationError("Thresholds must be greater than zero.", code="invalid_threshold")
    if not (0 < ungoverned_share_pct <= 100):
        raise ValidationError(
            "The ungoverned-line share must be a percentage above 0 and at most 100.",
            code="invalid_threshold",
        )

    before = await get_thresholds(db, org_id)
    row = await db.scalar(
        select(VatReliabilityThreshold).where(VatReliabilityThreshold.org_id == org_id)
    )
    if row is None:
        row = VatReliabilityThreshold(org_id=org_id)
        db.add(row)
    row.overcharge_cases = overcharge_cases
    row.overcharge_eur_per_1000 = overcharge_eur_per_1000
    row.fx_markup_bps = fx_markup_bps
    row.ungoverned_share_pct = ungoverned_share_pct
    await db.flush()

    await audit_svc.record(
        db,
        "transport.reliability_thresholds_set",
        target_type="organization",
        target_id=org_id,
        meta={
            "before": {
                "overcharge_cases": before.overcharge_cases,
                "overcharge_eur_per_1000": str(before.overcharge_eur_per_1000),
                "fx_markup_bps": before.fx_markup_bps,
                "ungoverned_share_pct": str(before.ungoverned_share_pct),
                "was_default": before.is_default,
            },
            "after": {
                "overcharge_cases": overcharge_cases,
                "overcharge_eur_per_1000": str(overcharge_eur_per_1000),
                "fx_markup_bps": fx_markup_bps,
                "ungoverned_share_pct": str(ungoverned_share_pct),
            },
        },
    )
    return await get_thresholds(db, org_id)


def _overcharge_criterion(
    claims: list[VatOverchargeClaim], spend_eur: Decimal, th: Thresholds
) -> Criterion:
    """Cases, euros detected, and the outcome split — normalised per €1,000 of
    net spend so a large supplier is not labelled unreliable for being large."""
    outcomes: dict[str, int] = {}
    for c in claims:
        outcomes[c.status] = outcomes.get(c.status, 0) + 1
    detected = sum((Decimal(c.detected_eur) for c in claims), Decimal("0"))
    per_1000 = q2(detected / spend_eur * Decimal("1000")) if spend_eur > 0 else Decimal("0.00")
    over = len(claims) >= th.overcharge_cases or per_1000 >= th.overcharge_eur_per_1000
    return Criterion(
        key=CRITERION_OVERCHARGES,
        band=_band(len(claims), over),
        rule=(
            f"'{BAND_RECURRING}' at {th.overcharge_cases} or more cases in the window, "
            f"or {th.overcharge_eur_per_1000} EUR or more detected per 1,000 EUR of net spend"
        ),
        figures={
            "cases": len(claims),
            "detected_eur": q2(detected),
            "detected_eur_per_1000_spend": per_1000,
            "outcomes": outcomes,
        },
    )


def _fx_criterion(rows: list[FuelTransaction], th: Thresholds) -> Criterion:
    """How the supplier converts: the median markup of the supplier's OWN
    stated rate over the ECB rate for the same day, in basis points.

    THE FINDING THIS DELIBERATELY DOES NOT COUNT. The design named a second
    one — a line whose euro has no established rate provenance — and building
    it proved it UNREPRESENTABLE: WO-88 refuses such a row at
    `fuel_ingest.ingest_transaction` and WO-89 added the matching CHECK
    (`ck_fuel_transactions_fx_provenance`), whose pre-flight refused to run
    while any offending row existed. A criterion that can never fire is worse
    than no criterion — it reads as a clean bill on a question nobody asked —
    so it is gone, and this comment is why. (The AP-side gap those orders left
    open, on `invoices`/`expense_items`, is arc 3's WO-V.)

    EUR-native lines are excluded from the denominator: no rate was involved,
    so including them would dilute the share toward zero for exactly the
    suppliers who bill in euros anyway.
    """
    foreign = [r for r in rows if (r.currency or "").upper() != "EUR"]
    markups_bps: list[Decimal] = []
    stated = 0
    for r in foreign:
        if r.fx_source == FX_STATED:
            stated += 1
            if r.fx_rate is not None and r.fx_ecb_rate is not None and Decimal(r.fx_ecb_rate) > 0:
                # Units-per-EUR (ADR-0010): a HIGHER stated rate buys fewer
                # euros per unit, so the markup is stated over ECB.
                delta = (Decimal(r.fx_rate) - Decimal(r.fx_ecb_rate)) / Decimal(r.fx_ecb_rate)
                markups_bps.append(delta * _BPS)

    median = _median(markups_bps)
    median_q = None if median is None else q2(median)
    findings = stated
    over = median is not None and median >= th.fx_markup_bps
    return Criterion(
        key=CRITERION_FX,
        band=_band(findings, over),
        rule=(
            f"'{BAND_RECURRING}' when the median markup of the supplier's own stated rate "
            f"over the ECB rate reaches {th.fx_markup_bps} basis points"
        ),
        figures={
            "foreign_currency_lines": len(foreign),
            "supplier_stated_rate_lines": stated,
            "median_markup_bps": median_q,
            "measured_lines": len(markups_bps),
        },
    )


def _ungoverned_criterion(
    audited: int, without_terms: int, breaches: int, th: Thresholds
) -> Criterion:
    """Lines charged that no agreement governs, plus the lines that breached
    one. Both are "charged something nobody agreed to" — the owner's third
    criterion — but they are reported separately because the remedies differ:
    an ungoverned line needs a term recorded, a breach needs chasing."""
    total = audited + without_terms
    findings = without_terms + breaches
    share = (
        (Decimal(findings) / Decimal(total) * Decimal("100")).quantize(_PCT_EXP)
        if total > 0
        else Decimal("0.00")
    )
    over = share >= th.ungoverned_share_pct
    return Criterion(
        key=CRITERION_UNGOVERNED,
        band=_band(findings, over),
        rule=(
            f"'{BAND_RECURRING}' when {th.ungoverned_share_pct}% or more of the supplier's "
            "validated lines are governed by no agreed term, or breached one"
        ),
        figures={
            "lines_total": total,
            "lines_without_agreed_terms": without_terms,
            "lines_breaching_a_term": breaches,
            "finding_share_pct": share,
        },
    )


async def report(db: AsyncSession, org_id: str, *, as_of: date | None = None) -> ReliabilityReport:
    """The whole board: one entry per supplier active in the window.

    Gate order, the transport entry-point pattern, fails CLOSED: the module
    entitlement first (ADR-P3 rule 3), then the derivation. Read-only — this
    function writes nothing, and the router that exposes it declares no
    mutating verb.
    """
    await _require_module(db, org_id)
    as_of = as_of or date.today()
    months = window_months(as_of)

    rows = list(await db.scalars(queries.fuel_transactions(org_id, months=months)))
    claims = list(
        await db.scalars(
            select(VatOverchargeClaim).where(
                VatOverchargeClaim.org_id == org_id,
                VatOverchargeClaim.period.in_(months),
            )
        )
    )
    th = await get_thresholds(db, org_id)

    by_supplier: dict[str, list[FuelTransaction]] = {}
    for r in rows:
        by_supplier.setdefault(r.supplier, []).append(r)
    claims_by_supplier: dict[str, list[VatOverchargeClaim]] = {}
    for c in claims:
        claims_by_supplier.setdefault(c.supplier, []).append(c)

    # Term coverage and breaches, per supplier, over the whole window — through
    # `contract_audit`'s OWN matcher rather than a re-implementation, because
    # there is exactly one €/L authority in this tree (see `_term_coverage`).
    coverage = await _term_coverage(db, org_id, by_supplier)

    entries: list[SupplierReliability] = []
    for supplier in sorted(by_supplier):
        srows = by_supplier[supplier]
        active_months = len({r.period for r in srows})
        spend = sum((Decimal(r.net_eur_eff) for r in srows), Decimal("0"))
        if active_months < MIN_ACTIVE_MONTHS:
            entries.append(
                SupplierReliability(
                    supplier=supplier,
                    overall=BAND_INSUFFICIENT,
                    active_months=active_months,
                    net_spend_eur=q2(spend),
                    criteria=(),
                )
            )
            continue
        governed_n, ungoverned_n, breaches_n = coverage.get(supplier, (0, 0, 0))
        crits = (
            _overcharge_criterion(claims_by_supplier.get(supplier, []), spend, th),
            _fx_criterion(srows, th),
            _ungoverned_criterion(governed_n, ungoverned_n, breaches_n, th),
        )
        entries.append(
            SupplierReliability(
                supplier=supplier,
                overall=_worst([c.band for c in crits]),
                active_months=active_months,
                net_spend_eur=q2(spend),
                criteria=crits,
            )
        )

    return ReliabilityReport(
        window_from=months[0],
        window_to=months[-1],
        framing=FRAMING,
        thresholds=th,
        suppliers=tuple(entries),
    )


async def _term_coverage(
    db: AsyncSession, org_id: str, by_supplier: dict[str, list[FuelTransaction]]
) -> dict[str, tuple[int, int, int]]:
    """Per supplier: (lines a term governs, lines no term governs, breaches).

    Coverage is decided by the SAME `contract_audit` term matcher — imported,
    not re-typed — so "governed" here can never drift from what the audit
    considers auditable. A zero-quantity line is skipped on both sides for the
    reason `contract_audit` documents: a €/L price is undefined for it.
    """
    terms = [t for t in await contract_audit.list_terms(db, org_id, active_only=True) if t.active]
    out: dict[str, tuple[int, int, int]] = {}
    for supplier, rows in by_supplier.items():
        governed = ungoverned = breaches = 0
        for txn in rows:
            if Decimal(txn.qty) <= 0:
                continue
            term = contract_audit._term_for(terms, txn)  # noqa: SLF001 — one matcher, on purpose
            if term is None:
                ungoverned += 1
                continue
            governed += 1
            if contract_audit._breach_for(txn, term) is not None:  # noqa: SLF001
                breaches += 1
        out[supplier] = (governed, ungoverned, breaches)
    return out


__all__ = [
    "BAND_CLEAN",
    "BAND_FINDINGS",
    "BAND_INSUFFICIENT",
    "BAND_RECURRING",
    "CRITERIA",
    "DEFAULT_THRESHOLDS",
    "FRAMING",
    "MIN_ACTIVE_MONTHS",
    "WINDOW_MONTHS",
    "Criterion",
    "ReliabilityReport",
    "SupplierReliability",
    "Thresholds",
    "get_thresholds",
    "report",
    "set_thresholds",
    "window_months",
]
