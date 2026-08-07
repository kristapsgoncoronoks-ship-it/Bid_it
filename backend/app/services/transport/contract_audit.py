"""Contract audit — "did the supplier invoice what it AGREED to?" (G4.5; R41;
`BA_fleet_fuel.md` §2.5 "Contract audit", §4.4, §3.G G1 / R49).

THE HARVESTED DEFINITION, VERBATIM
-----------------------------------
`BA_fleet_fuel.md` §2.5's "Contract audit" row is the whole business rule:

    "Two term types only: expected_discount_eur_l (rebate that should be
     applied, €/L) and max_net_eur_l (contracted NET price ceiling, €/L).
     Flags: 'short discount' (applied < expected − tol) and 'over ceiling'
     (eff_l > max + tol). recover_eur = gap × litres, dropped if ≤ 0.
     No volume-tier / stepped-rebate / annual-bonus / card-fee modelling."
     Constant: TOLERANCE = 0.005 €/L.

Every symbol in this module maps to one of those clauses, and the closing
sentence is a CONSTRAINT: a tiered or bonus-shaped contract is not modelled, so
it is not audited. The model refuses to approximate a term it cannot express.

THE PRICE BASIS — STATED, AS §3.G G1 REQUIRES
----------------------------------------------
**NET EUR/L, final — VAT excluded, rebates applied.** §3.G G1, verbatim:
*"Prices everywhere are NET EUR/L, FINAL — VAT excluded, rebates applied. This
basis must be stated on any new report surface. → NET/effective price =
`net_eur_eff / qty`."* R49 adds that both the as-invoiced and the effective
price are exposed *"so the rebate value is visible"*. Hence, per line:

    eur_l_doc = net_eur     / qty      (as invoiced — on-invoice discounts are
                                        already inside it, §4.2)
    eur_l_eff = net_eur_eff / qty      (effective — after every rebate layer,
                                        including off-invoice ones)
    applied   = eur_l_doc − eur_l_eff  (the rebate ACTUALLY applied, €/L)

That last identity is not an invention: §4.2's two-tier discount model says an
on-invoice discount is *"already inside `net_eur`"* and an off-invoice rebate
*"lands ONLY in `net_eur_eff`"*, so their per-litre difference IS the applied
rebate layer and nothing else.

WHAT G4.2/WO-84 CHANGED HERE (and what it deliberately did not)
-----------------------------------------------------------------
When this module shipped (WO-82) the identity above was arithmetically correct
and materially empty: `net_eur_eff` had no writer other than
`fuel_ingest`'s `= net_eur` default, so `applied` was identically `0.0000` and
every line governed by an `expected_discount_eur_l` term flagged `short
discount` for the FULL contracted rebate — including for a supplier that pays
its rebate off-invoice, which is exactly §4.2's canonical Q8/Port One case.
`app/services/transport/rebate.py` (G4.2, R50) now merges recorded off-invoice
rebate documents into `net_eur_eff` at the close, so `applied` is a real figure
and this module's findings shrink to the genuinely short ones.

**Not one line of the arithmetic below changed.** It reads a column that now
carries what its name always claimed. What this module gained is R50's second
half: `ContractAuditResult.source_warnings`, obtained by CALLING
`rebate.missing_source_warnings` (never a forked query — an expectation gets one
definition), advisory per §4.19, naming any (supplier, country) whose rebate
layer has gone missing so that a LIST-price `eur_l_eff` can never be published
here in silence.

`recover_eur = gap × litres`, dropped when it is not positive.

§4.14 — WHY THIS IS EUR-ONLY AND CANNOT BE A CROSS-CURRENCY SUM
-----------------------------------------------------------------
Both price columns this module reads are EUR by construction: ingestion refuses
a line whose currency it cannot convert (`fx_rate_unavailable`, the WO-69
two-phase guarantee), so a EUR figure exists for every stored row or the row
does not exist. A supplier month spanning several document currencies is
therefore natively summable **in EUR**, with no coercion of a foreign amount
anywhere — which is precisely what §4.14 requires. The three document-currency
amount columns are never read by this module (a structural test asserts their
absence by name); each finding carries its line's document currency as
PROVENANCE, and that value is never totalled. `ContractAuditResult.currency` is
a literal `"EUR"`, stated rather than assumed.

§4.9 — DECIMAL, AND WHERE THE ONE ROUNDING HAPPENS
----------------------------------------------------
Every €/L value is computed as an exact `Decimal` quotient and the breach test
is made on the UNROUNDED quotients, so a line sitting exactly on the tolerance
boundary can never flip because of a presentation rounding (the same discipline
`capture_review`'s tie-out comparison documents). The gap is then quantized ONCE
to `RATE_EXP` (4 dp — the rate precision, ten times finer than the 0.005 €/L
tolerance) and the money figure once more through `money.q2`:

    gap_eur_l   = q(agreed − actual, 0.0001)
    recover_eur = q2(gap_eur_l × qty)

so the reported gap, the reported litres and the reported euro reconcile exactly
on the evidence sheet the follow-up slice will render. The DISPLAY prices
(`eur_l_doc`, `eur_l_eff`, `agreed_eur_l`, `actual_eur_l`) are the same
quotients quantized to 4 dp for the wire; `gap_eur_l` is quantized from the
exact difference, not from those two already-rounded values, so it never
inherits two roundings.

TERM SELECTION — EXACTLY ONE TERM PER LINE
--------------------------------------------
A term's grain is `(supplier, country, station_like, product_group)` (§4.4). A
wildcard term (`station_like = ""`) and a station-specific one can both match a
line, and applying both would DOUBLE-COUNT `recover_eur` — a wrong money figure,
the exact class of error this surface exists to prevent. The spec supplies the
column but no tie-break, so this module states one (a DOCUMENTED
INTERPRETATION, `docs/plan/plan-a/wo/WO-82-overcharge-claimback.md`):
**most specific wins** — a non-empty pattern beats the wildcard; among non-empty
patterns the LONGEST wins; equal lengths break lexicographically so the outcome
is deterministic rather than dependent on row order. Exactly one term is ever
applied, and a line with no matching active term produces NO finding at all —
never a zero-gap "pass" row, and never a false positive (an unconfigured
supplier must read as "we have not told the system what was agreed", which
`lines_without_terms` reports as its own count).

READ-ONLY, AND WHY THAT IS STRUCTURAL
---------------------------------------
R41: *"Read-only over the analytics."* `audit()` and its helpers issue no write
of any kind — no `db.add`, no attribute assignment on a `FuelTransaction`, no
flush, no audit event — and a test compares every transaction row before and
after a run, column by column. Detection never re-rates a figure and never
gates anything (§4.19, and §3.L's advisory covenant over this whole analysis
family): the only thing that persists is a claim-back an operator opens
deliberately, in `overcharge.py`.

The TERM CRUD in this module DOES write — configuring an agreed term is an
ordinary audited mutation (§4.16, old→new). It lives here because the term and
the rule that reads it are one subject; `overcharge.py` imports this module,
never the reverse, so the read-only detection half stays free of the lifecycle.

THE LEGAL FRAMING IS PART OF THE OUTPUT (R53)
-----------------------------------------------
§2.4's framing table and R53 both insist the framings must not be flattened:
this analysis — alone among the price analyses — is **"Money the supplier
owes"**, a claim letter with a 30-day demand, whereas the same-day overpay
figure is *"negotiation evidence, NOT a contractual claim-back"*. `LEGAL_FRAMING`
and `PRICE_BASIS` therefore ride the result itself, so no surface can render the
number without its framing.

BOARD NOTE: `ARCH_plan.md`'s service file list names `overcharge.py` and not
`contract_audit.py`; the split is deliberate and matches how WO-81's recorded
deviation names the pair ("`contract_audit`/`overcharge.py`"). Keeping detection
in its own module is what makes "read-only over the analytics" a structural
property instead of a promise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionError, ValidationError
from app.core.money import q, q2
from app.models.transport.contract_term import ALL_STATIONS, VatSupplierContractTerm
from app.models.transport.fuel_transaction import PRODUCT_GROUPS, FuelTransaction
from app.services import audit as audit_svc
from app.services import modules
from app.services.transport import queries, rebate

# `BA_fleet_fuel.md` §2.5, verbatim: "TOLERANCE = 0.005 €/L". A module constant,
# not an env var: §2.5 names `AUDIT_TOLERANCE_EUR_L`, but a per-deployment env
# knob on a money threshold is a config-surface decision this codebase has taken
# for no other harvested constant (the R25 tolerances, DEADLINE_RISK_DAYS and the
# Art. 17 minimums are all module constants). The VALUE ships; the override does
# not. Recorded in the work order rather than silently dropped.
TOLERANCE = Decimal("0.005")

# The two harvested flag strings, verbatim (master-context §9 — actual
# vocabulary only; these are the words the spec uses, not a paraphrase).
FLAG_SHORT_DISCOUNT = "short discount"
FLAG_OVER_CEILING = "over ceiling"
FLAGS = (FLAG_SHORT_DISCOUNT, FLAG_OVER_CEILING)

# €/L rate precision — see the module docstring's rounding section.
RATE_EXP = Decimal("0.0001")

# §3.G G1 / R49: "this basis must be stated on any new report surface".
PRICE_BASIS = "NET EUR/L, final — VAT excluded, rebates applied"
# §2.4's framing table / R53 — the framing that must never be flattened.
LEGAL_FRAMING = "Money the supplier owes — a contractual claim-back, not negotiation evidence"

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True)
class Breach:
    """One line that breaches one agreed term. Basis: NET EUR/L, final — VAT
    excluded, rebates applied. `document_currency` is PROVENANCE only: it is
    never summed and no amount here is expressed in it (§4.14)."""

    fuel_transaction_id: str
    entity_id: str
    supplier: str
    country: str
    station: str
    product_group: str
    txn_date: date
    qty: Decimal
    document_currency: str
    # Both prices exposed "so the rebate value is visible" (R49).
    eur_l_doc: Decimal
    eur_l_eff: Decimal
    flag: str
    # The agreed figure (the expected discount, or the ceiling) and what the
    # line actually delivered against it (the applied discount, or eur_l_eff).
    agreed_eur_l: Decimal
    actual_eur_l: Decimal
    gap_eur_l: Decimal
    recover_eur: Decimal


@dataclass(frozen=True)
class ContractAuditResult:
    """`recover_eur` is the €-exposure DETECTED, not cash recovered — the
    booked-cash north star is `overcharge.recovered_total` (§2.4). Reported
    together only ever under distinct names (R52's label-them-distinctly
    discipline applied to two figures that are supposed to differ)."""

    period: str
    supplier: str | None
    currency: str
    price_basis: str
    legal_framing: str
    breaches: tuple[Breach, ...]
    recover_eur: Decimal
    lines_audited: int
    lines_without_terms: int
    lines_skipped_zero_qty: int
    # R50's source guard, ADVISORY (§4.19) — one string per (supplier, country)
    # whose off-invoice rebate layer has GONE MISSING for this period (it
    # carried a recorded rebate document before, has litres now, and has no
    # recorded source for this month). It changes no euro above and blocks
    # nothing; it exists so that R50's *"it does not silently produce
    # list-price analytics"* is literally true AT the surface that produces
    # them. `eur_l_eff` on every Breach below is a LIST price wherever one of
    # these fires. Empty for a supplier that never had an off-invoice layer —
    # with no history there is no expectation (see
    # `rebate.missing_source_warnings`).
    source_warnings: tuple[str, ...] = ()


async def _require_module(db: AsyncSession, org_id: str) -> None:
    """`modules.is_enabled`, never `require_enabled` — a service signals with
    `AppError`, not `fastapi.HTTPException` (ADR-P3 rule 3; the entitlement is
    checked INSIDE the service, never delegated to the route, and fails
    CLOSED)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


def validate_period(period: str) -> None:
    """`"YYYY-MM"` — `FuelTransaction.period`'s own shape. Fails CLOSED for the
    reason `fuel.resolve_period_months` gives: silently auditing an empty set
    for a typo'd month looks exactly like "this supplier honoured every term",
    which is a materially different and dangerous answer."""
    if not _PERIOD_RE.match(period):
        raise ValidationError(
            f"'{period}' is not a valid period — use YYYY-MM", code="invalid_period"
        )


def _validate_term_key(country: str, product_group: str) -> str:
    if not _COUNTRY_RE.match(country.upper()):
        raise ValidationError(
            f"'{country}' is not a valid country — use an ISO 3166-1 alpha-2 code",
            code="invalid_country",
        )
    if product_group not in PRODUCT_GROUPS:
        raise ValidationError(
            f"'{product_group}' is not a valid product group — use one of: "
            + ", ".join(PRODUCT_GROUPS),
            code="invalid_product_group",
        )
    return country.upper()


def _validate_rates(
    expected_discount_eur_l: Decimal | None, max_net_eur_l: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """§2.5's "two term types only" — a row must assert at least one of them,
    and each must be a sane €/L rate. A term asserting nothing would out-rank a
    broader term (the specificity rule) while auditing against nothing, so it is
    refused here AND by the table's own CHECK constraint (defense in depth)."""
    if expected_discount_eur_l is None and max_net_eur_l is None:
        raise ValidationError(
            "A contract term must set an expected discount, a net price ceiling, or both",
            code="term_has_no_figure",
        )
    if expected_discount_eur_l is not None and expected_discount_eur_l < 0:
        raise ValidationError(
            "The expected discount must not be negative (it is a rebate in €/L)",
            code="invalid_term_rate",
        )
    if max_net_eur_l is not None and max_net_eur_l <= 0:
        raise ValidationError(
            "The net price ceiling must be greater than zero (€/L)", code="invalid_term_rate"
        )
    return (
        None if expected_discount_eur_l is None else q(expected_discount_eur_l, RATE_EXP),
        None if max_net_eur_l is None else q(max_net_eur_l, RATE_EXP),
    )


async def get_term(
    db: AsyncSession,
    org_id: str,
    *,
    supplier: str,
    country: str,
    product_group: str,
    station_like: str = ALL_STATIONS,
) -> VatSupplierContractTerm | None:
    """The term on the exact natural key, or None. A single-key SELECT — the
    R21 marker-only discipline: no fuzzy supplier pairing anywhere."""
    return await db.scalar(
        select(VatSupplierContractTerm).where(
            VatSupplierContractTerm.org_id == org_id,
            VatSupplierContractTerm.supplier == supplier,
            VatSupplierContractTerm.country == country.upper(),
            VatSupplierContractTerm.product_group == product_group,
            VatSupplierContractTerm.station_like == station_like,
        )
    )


async def set_term(
    db: AsyncSession,
    org_id: str,
    *,
    supplier: str,
    country: str,
    product_group: str,
    station_like: str = ALL_STATIONS,
    expected_discount_eur_l: Decimal | None = None,
    max_net_eur_l: Decimal | None = None,
    active: bool = True,
) -> VatSupplierContractTerm:
    """Create or update the agreed term on the harvested natural key — the ONLY
    writer of `VatSupplierContractTerm` alongside `remove_term`.

    Idempotent: a repeat call with identical values returns the existing row and
    audits nothing (the `checklist.set_active` no-op convention); any changed
    value audits old→new IN THE SAME transaction as the mutation (§4.16), so a
    change to a figure that determines real money always has an actor.
    """
    await _require_module(db, org_id)
    country = _validate_term_key(country, product_group)
    expected, ceiling = _validate_rates(expected_discount_eur_l, max_net_eur_l)

    existing = await get_term(
        db,
        org_id,
        supplier=supplier,
        country=country,
        product_group=product_group,
        station_like=station_like,
    )
    new = {
        "expected_discount_eur_l": expected,
        "max_net_eur_l": ceiling,
        "active": active,
    }
    if existing is not None:
        old = {
            "expected_discount_eur_l": existing.expected_discount_eur_l,
            "max_net_eur_l": existing.max_net_eur_l,
            "active": existing.active,
        }
        if all(old[k] == new[k] for k in new):
            return existing
        existing.expected_discount_eur_l = expected
        existing.max_net_eur_l = ceiling
        existing.active = active
        await db.flush()
        await audit_svc.record(
            db,
            audit_svc.A.TRANSPORT_CONTRACT_TERM_SET,
            target_type="vat_supplier_contract_term",
            target_id=existing.id,
            meta={
                "supplier": supplier,
                "country": country,
                "station_like": station_like,
                "product_group": product_group,
                "old": {k: str(v) for k, v in old.items()},
                "new": {k: str(v) for k, v in new.items()},
            },
            org_id=org_id,
        )
        return existing

    row = VatSupplierContractTerm(
        org_id=org_id,
        supplier=supplier,
        country=country,
        station_like=station_like,
        product_group=product_group,
        expected_discount_eur_l=expected,
        max_net_eur_l=ceiling,
        active=active,
    )
    db.add(row)
    await db.flush()
    await audit_svc.record(
        db,
        audit_svc.A.TRANSPORT_CONTRACT_TERM_SET,
        target_type="vat_supplier_contract_term",
        target_id=row.id,
        meta={
            "supplier": supplier,
            "country": country,
            "station_like": station_like,
            "product_group": product_group,
            "old": None,
            "new": {k: str(v) for k, v in new.items()},
        },
        org_id=org_id,
    )
    return row


async def remove_term(
    db: AsyncSession,
    org_id: str,
    *,
    supplier: str,
    country: str,
    product_group: str,
    station_like: str = ALL_STATIONS,
) -> bool:
    """Delete a mis-keyed term. Returns False when there was nothing to remove
    (a no-op audits nothing).

    Deletion is for a MISTAKE. A contract that simply lapsed should be
    deactivated (`set_term(..., active=False)`) so a past period can still be
    audited against the terms that applied to it — the "kept forever" reasoning
    §2.5 gives for `advertised_prices`, applied to the term side.
    """
    await _require_module(db, org_id)
    existing = await get_term(
        db,
        org_id,
        supplier=supplier,
        country=country,
        product_group=product_group,
        station_like=station_like,
    )
    if existing is None:
        return False
    old = {
        "expected_discount_eur_l": str(existing.expected_discount_eur_l),
        "max_net_eur_l": str(existing.max_net_eur_l),
        "active": str(existing.active),
    }
    row_id = existing.id
    await db.delete(existing)
    await db.flush()
    await audit_svc.record(
        db,
        audit_svc.A.TRANSPORT_CONTRACT_TERM_REMOVE,
        target_type="vat_supplier_contract_term",
        target_id=row_id,
        meta={
            "supplier": supplier,
            "country": country.upper(),
            "station_like": station_like,
            "product_group": product_group,
            "old": old,
            "new": None,
        },
        org_id=org_id,
    )
    return True


async def list_terms(
    db: AsyncSession,
    org_id: str,
    *,
    supplier: str | None = None,
    country: str | None = None,
    active_only: bool = False,
) -> list[VatSupplierContractTerm]:
    """The configured terms, org-scoped. Ordering is the natural key so the list
    is stable and a station-specific term reads next to the wildcard it
    out-ranks."""
    await _require_module(db, org_id)
    where = [VatSupplierContractTerm.org_id == org_id]
    if supplier:
        where.append(VatSupplierContractTerm.supplier == supplier)
    if country:
        where.append(VatSupplierContractTerm.country == country.upper())
    if active_only:
        where.append(VatSupplierContractTerm.active.is_(True))
    rows = await db.scalars(
        select(VatSupplierContractTerm)
        .where(*where)
        .order_by(
            VatSupplierContractTerm.supplier,
            VatSupplierContractTerm.country,
            VatSupplierContractTerm.product_group,
            VatSupplierContractTerm.station_like,
        )
    )
    return list(rows)


# --------------------------------------------------------------------------- #
# Detection — READ-ONLY from here down
# --------------------------------------------------------------------------- #


def _station_matches(pattern: str, station: str) -> bool:
    """SQL `LIKE` semantics for the harvested `station_like` column: `%` matches
    any run, `_` matches one character, everything else is literal, matched
    case-insensitively (station text arrives in supplier vocabulary and casing).
    `""` is the wildcard — it matches every station."""
    if pattern == ALL_STATIONS:
        return True
    parts = []
    for ch in pattern:
        if ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return re.fullmatch("".join(parts), station or "", re.IGNORECASE) is not None


def _specificity(term: VatSupplierContractTerm) -> tuple[int, int, str]:
    """The most-specific-wins ordering (see the module docstring): a non-empty
    pattern beats the wildcard, a longer pattern beats a shorter one, and the
    pattern text itself breaks a tie so the winner never depends on row order."""
    pattern = term.station_like
    return (0 if pattern == ALL_STATIONS else 1, len(pattern), pattern)


def _term_for(
    terms: list[VatSupplierContractTerm], txn: FuelTransaction
) -> VatSupplierContractTerm | None:
    """The ONE active term that governs this line, or None. Exactly one is ever
    returned — applying two overlapping terms would double-count `recover_eur`
    (see the module docstring)."""
    matching = [
        t
        for t in terms
        if t.supplier == txn.supplier
        and t.country == txn.country
        and t.product_group == txn.product_group
        and _station_matches(t.station_like, txn.station)
    ]
    if not matching:
        return None
    return max(matching, key=_specificity)


def _breach_for(txn: FuelTransaction, term: VatSupplierContractTerm) -> Breach | None:
    """§2.5's two flags, on ONE line, against ONE term — or None when the line
    honours the term.

    The comparisons are made on the UNROUNDED quotients so a line sitting
    exactly on the tolerance boundary cannot flip on a presentation rounding;
    the gap is quantized once to `RATE_EXP` and the euro once through `q2`.
    `recover_eur = gap × litres`, "dropped if ≤ 0" (verbatim) — which is also
    what makes the boundary case (`applied == expected`, `eur_l_eff == max`)
    produce nothing at all rather than a zero-euro finding.

    Both term types may be set on one row. The two flags are mutually exclusive
    per line by construction of the arithmetic — a line cannot simultaneously be
    over a NET ceiling and short of a rebate by the same gap — so the short
    discount is evaluated first (it is the term type §2.5 lists first) and the
    ceiling only when the discount side is satisfied.
    """
    qty = Decimal(txn.qty)
    eur_l_doc = Decimal(txn.net_eur) / qty
    eur_l_eff = Decimal(txn.net_eur_eff) / qty

    agreed: Decimal
    actual: Decimal
    flag: str
    if term.expected_discount_eur_l is not None:
        agreed = Decimal(term.expected_discount_eur_l)
        actual = eur_l_doc - eur_l_eff  # the rebate ACTUALLY applied, §4.2
        if actual < agreed - TOLERANCE:
            flag = FLAG_SHORT_DISCOUNT
            gap = q(agreed - actual, RATE_EXP)
            return _finding(txn, flag, agreed, actual, gap, eur_l_doc, eur_l_eff, qty)

    if term.max_net_eur_l is not None:
        agreed = Decimal(term.max_net_eur_l)
        actual = eur_l_eff
        if actual > agreed + TOLERANCE:
            flag = FLAG_OVER_CEILING
            gap = q(actual - agreed, RATE_EXP)
            return _finding(txn, flag, agreed, actual, gap, eur_l_doc, eur_l_eff, qty)

    return None


def _finding(
    txn: FuelTransaction,
    flag: str,
    agreed: Decimal,
    actual: Decimal,
    gap: Decimal,
    eur_l_doc: Decimal,
    eur_l_eff: Decimal,
    qty: Decimal,
) -> Breach | None:
    """Shape one `Breach`, applying §2.5's "dropped if ≤ 0" to the euro."""
    recover = q2(gap * qty)
    if recover <= 0:
        return None
    return Breach(
        fuel_transaction_id=txn.id,
        entity_id=txn.entity_id,
        supplier=txn.supplier,
        country=txn.country,
        station=txn.station,
        product_group=txn.product_group,
        txn_date=txn.txn_date,
        qty=qty,
        document_currency=txn.currency,
        eur_l_doc=q(eur_l_doc, RATE_EXP),
        eur_l_eff=q(eur_l_eff, RATE_EXP),
        flag=flag,
        agreed_eur_l=q(agreed, RATE_EXP),
        actual_eur_l=q(actual, RATE_EXP),
        gap_eur_l=gap,
        recover_eur=recover,
    )


async def audit(
    db: AsyncSession,
    org_id: str,
    *,
    period: str,
    supplier: str | None = None,
) -> ContractAuditResult:
    """R41's detection half — every validated fuel line of one accounting month
    checked against the agreed terms, READ-ONLY.

    Basis: **NET EUR/L, final — VAT excluded, rebates applied** (§3.G G1 / R49).
    Currency: **EUR only**, and never a coerced foreign amount — see the module
    docstring's §4.14 section.

    Gate order, the transport-wide entry-point pattern, fails CLOSED:
    1. the `transport` module entitlement (ADR-P3 rule 3);
    2. `period` validation (422 `invalid_period`).

    This is the ONE line source R41 requires both send-ready artifacts (the
    Excel evidence packet and the PDF claim letter — a follow-up slice of G4.5)
    to render from, and it is the same source `overcharge.open_claim` snapshots,
    which is what makes "both artifacts show identical lines and totals"
    structural rather than coincidental.

    Every term is loaded ONCE per run and matched in Python: the term table is
    admin-curated configuration measured in tens of rows, while a month of fuel
    lines is measured in thousands, so a per-line term query would be the exact
    N+1 the receipt-control module already documents avoiding. Nothing here is
    a forked query of anything: no other module derives €/L.

    A line with `qty <= 0` is SKIPPED and counted separately. §4.2 is explicit
    that quantity "can be 0 (promo correction lines)", a €/L price is undefined
    for it, and a zero-litre line cannot breach a per-litre term — reporting it
    as a pass or a breach would both be wrong.
    """
    await _require_module(db, org_id)
    validate_period(period)

    terms = [
        t for t in await list_terms(db, org_id, supplier=supplier, active_only=True) if t.active
    ]

    # `supplier or None` preserves this call site's pre-registry convention
    # exactly: it used `if supplier:`, so an empty string meant "every
    # supplier" (the registry's own convention is `is not None`).
    rows = list(
        await db.scalars(
            queries.fuel_transactions(org_id, period=period, supplier=supplier or None).order_by(
                FuelTransaction.supplier,
                FuelTransaction.txn_date,
                FuelTransaction.line_seq,
            )
        )
    )

    breaches: list[Breach] = []
    audited = 0
    without_terms = 0
    skipped_zero_qty = 0
    for txn in rows:
        if Decimal(txn.qty) <= 0:
            skipped_zero_qty += 1
            continue
        term = _term_for(terms, txn)
        if term is None:
            without_terms += 1
            continue
        audited += 1
        breach = _breach_for(txn, term)
        if breach is not None:
            breaches.append(breach)

    return ContractAuditResult(
        period=period,
        supplier=supplier,
        currency="EUR",
        price_basis=PRICE_BASIS,
        legal_framing=LEGAL_FRAMING,
        breaches=tuple(breaches),
        recover_eur=q2(sum((b.recover_eur for b in breaches), Decimal("0"))),
        lines_audited=audited,
        lines_without_terms=without_terms,
        lines_skipped_zero_qty=skipped_zero_qty,
        # R50 (G4.2/WO-84): the off-invoice rebate SOURCE GUARD, read straight
        # off `rebate` rather than reimplemented — this module never forks a
        # query, and a rebate expectation must have exactly one definition.
        source_warnings=await rebate.missing_source_warnings(
            db, org_id, period=period, supplier=supplier
        ),
    )


__all__ = [
    "ALL_STATIONS",
    "FLAGS",
    "FLAG_OVER_CEILING",
    "FLAG_SHORT_DISCOUNT",
    "LEGAL_FRAMING",
    "PRICE_BASIS",
    "RATE_EXP",
    "TOLERANCE",
    "Breach",
    "ContractAuditResult",
    "audit",
    "get_term",
    "list_terms",
    "remove_term",
    "set_term",
    "validate_period",
]
