"""The contingency fee: the formula, the configured rate, and the freeze
(G2.9; R13; `BA_fleet_fuel.md` §2.2, C10, C11, R40).

WHAT THE FEE IS, IN THE SPEC'S OWN WORDS
------------------------------------------
The transport vertical is sold no-win-no-fee. `BA_fleet_fuel.md` line 41 frames
the business (*"…on behalf of transport companies, and charges a **contingency
fee**"*), line 125 explains why the subscription price is zero (*"`tax_refund`
is **€0/mo** — deliberately monetised as the `/fees` contingency"*), and §2.2
states the arithmetic and its timing in two lines, verbatim:

    fee = max(fee_pct% × base, fee_min)   [customer_master.compute_fee]
    rate FROZEN at submission; fee CHARGED on the PAID amount

**C10 — FEE FREEZING**, verbatim: *"On first entry to a locking state: freeze
`vat_eur` **and** `vat_local` computed over **exactly the locked `claim_set`**
… Freeze `fee_pct`, `fee_min`, `fee_eur`."* And: *"Only the fee BASE changes
(claimed → paid); the frozen rate/minimum are never re-derived. % / minimum
changes only affect *un-submitted* declarations."*

**C11 — the formula**, verbatim: *"`% fee` takes priority; if it falls below the
per-declaration minimum, the **minimum** is charged. Returns `(fee, basis)` where
basis ∈ {`percent`, `minimum`}. Resolution order for the rate (`fee_for`):
per-(customer, country) override → customer default → (0, 0)."*

**R13**, the M-priority rule: *"Fee rate FROZEN at first submission; fee CHARGED
on the PAID amount."* Its acceptance line is the invariant this module exists to
make true: *"Change the customer fee % after submission ⇒ the claim's fee is
unchanged."*

**R40** names the rung above the customer: *"Standard fee is admin-editable; a
per-client fee overrides it."*

THE ENGINE FAILS CLOSED WHEN NO RATE IS CONFIGURED — AND WHY
---------------------------------------------------------------
This is the single most consequential decision in this module, so it is argued
here rather than assumed.

`docs/DECISIONS-NEEDED.md` §10 was resolved on 2026-08-08: the MODEL is a
contingency fee on recovered VAT. The **percentage and the minimum were
explicitly left open**, and the decision text says the mechanism is to be built
*"with the rate as an org-level setting that FAILS CLOSED when unset — a fee
figure is what a client is charged, so no default may be invented."*

So two things in the harvest are deliberately NOT reproduced:

1. **`BA_fleet_fuel.md` Appendix B's `pricing_fee_pct 15%`.** A number carried
   over from a retired single-tenant system is not a decision about what this
   product charges. Master-context §10 forbids inventing a commercial fact.
2. **C11's terminal `(0, 0)` rung.** Fleet Fuel resolved an unconfigured
   customer to a zero rate and a zero minimum, which freezes `fee_eur = 0.00`
   onto the claim. Once frozen, that value is indistinguishable from a
   deliberate zero-fee arrangement: the claim carries a positive assertion
   *"this filing earns no fee"* that nobody made. Under no-win-no-fee the fee is
   the entire revenue of the vertical, so a silent zero either forfeits it or is
   discovered later and "corrected" by rewriting a frozen figure — which is
   precisely what R13 exists to prevent.

`resolve_fee_rate` therefore ends in a refusal (`fee_rate_not_configured`)
instead of a number, and because `lock.submit_claim` calls it as a gate, an org
with no configured rate **cannot submit a claim at all**. That is a real cost —
a statutory filing blocked on a commercial setting — and it is accepted with
open eyes: the refusal names the entity and the country and tells the operator
exactly what to configure, configuring it is one audited service call, and the
alternative is filing under a rate nobody chose.

**The diesel-excise precedent does not transfer.** `excise.py` ships
`DEFAULT_RATE_EUR_PER_1000L = 30.00` as an explicit placeholder, and that is
right there for two reasons this module has neither of: the excise figure is
**advisory** (R42: *"the figure asserts NO eligibility"*, surfaced loudly on
every screen that shows it), and the rate is **the state's**, not ours — a wrong
one misstates a third party's published figure, which the customs filing itself
would correct. A fee percentage is ours, it is binding, and the first place a
wrong one surfaces is an invoice a client pays. An indicative rate on an
advisory figure is not the same object as a live charge.

**An explicitly typed zero is accepted, and that is not a contradiction.** The
refusal is about ABSENCE. A `(0, 0)` row was typed by a human, is audited with
an actor and an old→new pair, and states a real arrangement. Absence states
nothing. That distinction is the whole design.

WHERE THE FREEZE HAPPENS, AND WHY IN TWO PIECES
--------------------------------------------------
`lock.submit_claim` is C10's *"first entry to a locking state"*. This module
splits its work across that function deliberately:

* `resolve_fee_rate` is a **pure read** and runs as the LAST GATE, after every
  statutory gate and BEFORE `freeze.freeze_claim_lines`. That keeps
  `submit_claim`'s own contract exactly true — *"a claim that fails ANY gate
  never freezes or locks at all — nothing is mutated before the LAST gate
  passes"* — so a missing rate refuses while the session is still unmutated.
  It runs last among the gates because a legal problem (period not ended, a
  missing document, a below-minimum claim) is what an operator most needs to
  hear about first; our own billing configuration is the least urgent refusal.
* `freeze_fee` then stamps the three columns immediately AFTER the line freeze,
  in the same flush as the lock inserts and the status flip. C10 names the VAT
  base and the fee in one sentence because they are one atomic act: a lost lock
  race must roll back the fee with everything else, and a successful submission
  must never leave a frozen base beside an unfrozen fee.

`freeze_fee` reads `claim.vat_eur` — the base `freeze_claim_lines` computed over
exactly the claim's own lines (C10's first bullet, R13's *"not a period SUM"*) —
and never re-derives it. `compute_fee` is a pure function so the R40 pricing
calculator can produce the same figure from the same inputs without a second
implementation (*"reusing the same fee function the real claim uses"*).

CURRENCY BASIS: NET EUR, AND NOTHING ELSE
--------------------------------------------
The base is `claim.vat_eur` and `fee_min` is EUR, so the fee is a single-currency
computation and no cross-currency aggregation ever occurs (master-context
§4.14). `claim.vat_local` is deliberately never a fee input: it is denominated in
the refunding state's currency, it varies per claim, and a minimum stated in EUR
cannot be compared against it without a recorded conversion this module does not
have. A test asserts by AST scan that this module never reads `vat_local`.

WITHDRAWAL DOES NOT CLEAR A FROZEN FEE — THE SPEC'S ANSWER
-------------------------------------------------------------
§3.D **D7** enumerates exactly what a withdrawal changes: *"Only `withdraw_claim`
releases, and it also NULLs `status_code`."* The locks and the code — nothing
else. Nothing anywhere in the harvest clears a frozen figure on withdrawal, and
`freeze_claim_lines`' own `vat_eur`/`vat_local`/`currency` are correspondingly
left standing by `lock.withdraw_claim` today. Clearing the fee while leaving the
base it was computed from would put the claim in a shape the specification never
describes.

The point is settled beyond interpretation by the lifecycle itself:
`submit_claim` refuses any claim whose status is not `draft`, so a withdrawn
claim can never be re-submitted (pinned by WO-94's
`test_wo94_a_withdrawn_claim_cannot_be_resubmitted_or_recoded`). A frozen fee can
therefore never be re-frozen, contradicted or made stale by a later filing.
`withdraw_claim` is untouched by this module, and a WO-95 test pins the survival
so the reading is proven rather than merely argued.

THE PARTIAL-REJECTION SEAM — DOCUMENTED, NOT BUILT
-----------------------------------------------------
`status.py`'s docstring records that D2's `ENGINE_OF` engine-state transitions
(*"money received"/"decision received"/"rejected"*) collide with this board, and
`docs/DECISIONS-NEEDED.md`'s 2026-08-08 §13 note confirms partial rejection as a
capability that *"does not exist yet"* and a separate owner-confirmed follow-up.
**Nothing here implements it.**

What such a transition would need from this module, stated so the next order does
not have to rediscover it: C10's second bullet recomputes the fee on a CHANGED
BASE at the FROZEN rate (*"Only the fee BASE changes (claimed → paid); the frozen
rate/minimum are never re-derived"*). That is why `compute_fee` takes the rate as
arguments rather than resolving it internally, and why `fee_pct`/`fee_min` are
stored on the CLAIM rather than only in `vat_fee_rates`: a recompute reads the
claim's own frozen pair and calls `compute_fee` again with a smaller base. No
function, no column and no status transition for it is added here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionError, ValidationError
from app.core.money import q2
from app.models.transport.fee_rate import ANY_COUNTRY, VatFeeRate
from app.models.transport.vat_claim import VatRefundClaim
from app.services import audit as audit_svc
from app.services import issuer, modules

# C11's two-value basis vocabulary — the spec's own words, no third value
# (master-context §9: actual vocabulary only).
BASIS_PERCENT = "percent"
BASIS_MINIMUM = "minimum"

# A percentage is out of 100; the divisor is named so the arithmetic reads as
# the formula rather than as a magic literal.
_HUNDRED = Decimal("100")

# The scope labels the audit meta uses to record WHICH rung a rate was resolved
# or written at — internal vocabulary for an audit consumer, never on any wire.
SCOPE_CUSTOMER_COUNTRY = "customer_country"
SCOPE_CUSTOMER = "customer"
SCOPE_ORG = "org"


@dataclass(frozen=True)
class FeeRate:
    """One resolved `(fee_pct, fee_min)` pair and the rung it came from.

    `scope` is carried for the audit trail only: knowing that a claim was billed
    at the ORG STANDARD rather than at a negotiated per-client rate is exactly
    the kind of fact a fee dispute turns on, and reconstructing it later from the
    rate table would be wrong the moment the table changed.
    """

    fee_pct: Decimal
    fee_min: Decimal
    scope: str


def compute_fee(base: Decimal, fee_pct: Decimal, fee_min: Decimal) -> tuple[Decimal, str]:
    """C11's formula, verbatim: *"`% fee` takes priority; if it falls below the
    per-declaration minimum, the **minimum** is charged. Returns `(fee, basis)`
    where basis ∈ {`percent`, `minimum`}."*

    Pure, synchronous and Decimal-only (master-context §4.9, `money.q2`,
    ROUND_HALF_UP) so the R40 pricing calculator and any future paid-amount
    recompute call THIS function rather than reimplementing it — R40's own
    acceptance line: *"The calculator and a real claim produce the same fee for
    the same inputs."*

    THE EXACT TIE BELONGS TO `percent`. C11 says the minimum applies when the
    percentage *"falls below"* it; a percentage exactly EQUAL to the minimum has
    not fallen below it. The euro figure is identical either way — only the
    reported basis differs — but the basis is what an invoice line says it is
    charging, and "15% of your refund" is the true description of a fee that the
    percentage produced on its own.

    `base` is NET EUR (`claim.vat_eur`) and `fee_min` is NET EUR; there is no
    currency conversion anywhere in this function and none is possible (§4.14).
    """
    percent_fee = q2(Decimal(base) * Decimal(fee_pct) / _HUNDRED)
    minimum = q2(Decimal(fee_min))
    if percent_fee < minimum:
        return minimum, BASIS_MINIMUM
    return percent_fee, BASIS_PERCENT


async def _require_module(db: AsyncSession, org_id: str) -> None:
    """The transport entry-point gate, fails CLOSED — every transport service
    checks the entitlement itself rather than trusting its caller, so an
    un-entitled org stays byte-identical to before the vertical existed
    (ADR-P3 rule 3). `modules.is_enabled` (a bool), never `require_enabled`
    (which raises `fastapi.HTTPException` — the wrong shape for a service)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


def _validate_pct(fee_pct: Decimal) -> Decimal:
    """0 <= pct <= 100, quantized to the storage scale. Zero is deliberately
    LEGAL — see the module docstring on why an explicitly typed zero is a
    decision and an absent rate is not. Above 100 is refused: a contingency fee
    larger than the whole recovery would bill a client more than the refund, and
    that is not an arrangement this product supports."""
    pct = q2(Decimal(fee_pct))
    if pct < 0 or pct > _HUNDRED:
        raise ValidationError(
            f"Fee percentage must be between 0 and 100, got {pct}", code="invalid_fee_pct"
        )
    return pct


def _validate_min(fee_min: Decimal) -> Decimal:
    """A per-declaration minimum in NET EUR; zero is legal (no minimum),
    negative is not — a negative minimum would mean the floor of a charge is a
    refund to the client, which C11's `max()` cannot express."""
    minimum = q2(Decimal(fee_min))
    if minimum < 0:
        raise ValidationError(
            f"Fee minimum cannot be negative, got {minimum}", code="invalid_fee_min"
        )
    return minimum


def _normalise_country(country: str | None) -> str:
    """`None`/empty -> `ANY_COUNTRY` (the "every country" sentinel, C11's
    customer-default rung); anything else must be a 2-letter code, upper-cased
    to match `VatRefundClaim.refund_country`'s own normalisation in
    `claim.get_or_create_claim`."""
    if country is None or country == ANY_COUNTRY:
        return ANY_COUNTRY
    value = country.strip().upper()
    if len(value) != 2 or not value.isalpha():
        raise ValidationError(
            f"'{country}' is not an ISO 3166-1 alpha-2 country code", code="invalid_country"
        )
    return value


async def _require_entity(db: AsyncSession, org_id: str, entity_id: str) -> None:
    """Reads the AP/AR core through its OWN service (`issuer.get_by_id`), never a
    raw cross-domain join (ADR-P3 rule 2). A cross-tenant entity id is
    indistinguishable from an unknown one (§4.4: opaque 404, never 403)."""
    if await issuer.get_by_id(db, org_id, entity_id) is None:
        raise NotFoundError("Entity not found", code="entity_not_found")


async def get_rate_row(
    db: AsyncSession, org_id: str, *, entity_id: str | None, country: str
) -> VatFeeRate | None:
    """One exact rung, or `None`. `entity_id=None` addresses the org standard."""
    where = [VatFeeRate.org_id == org_id, VatFeeRate.country == country]
    where.append(
        VatFeeRate.entity_id.is_(None) if entity_id is None else VatFeeRate.entity_id == entity_id
    )
    return await db.scalar(select(VatFeeRate).where(*where))


async def list_rates(db: AsyncSession, org_id: str) -> list[VatFeeRate]:
    """Every configured rate in the org, most specific first (the same order
    `resolve_fee_rate` walks), so a reader sees the chain as it actually
    resolves rather than in insertion order."""
    await _require_module(db, org_id)
    rows = list(
        await db.scalars(
            select(VatFeeRate)
            .where(VatFeeRate.org_id == org_id)
            .order_by(VatFeeRate.entity_id, VatFeeRate.country, VatFeeRate.id)
        )
    )
    # NULL entity_id (the org standard) sorts LAST — it is the least specific
    # rung, and SQL's own NULL ordering differs between SQLite and PostgreSQL.
    return sorted(rows, key=lambda r: (r.entity_id is None, r.entity_id or "", r.country == ""))


async def resolve_fee_rate(
    db: AsyncSession, org_id: str, *, entity_id: str, country: str
) -> FeeRate:
    """C11's resolution chain — *"per-(customer, country) override → customer
    default"* — plus R40's/the 2026-08-08 decision's org-level STANDARD rate,
    and then a REFUSAL where C11 had `(0, 0)`:

        (entity, country)  ->  (entity, '')  ->  (NULL, '')  ->  refuse

    The customer IS the claimant entity: WO-73 (R44) shipped
    `VatCustomerLifecycle`, keyed `(org, entity_id)` and named for the customer,
    and `lock.submit_claim` already refuses a claim whose entity is not an
    `active` customer with the refund country separately activated. No second
    customer identity is invented here (master-context §10).

    Raises `ConflictError(code="fee_rate_not_configured")` when nothing matches.
    Read-only — see the module docstring for the full argument on why this fails
    CLOSED rather than defaulting, and why the excise placeholder precedent does
    not transfer.
    """
    await _require_module(db, org_id)
    country = _normalise_country(country)

    for candidate_entity, candidate_country, scope in (
        (entity_id, country, SCOPE_CUSTOMER_COUNTRY),
        (entity_id, ANY_COUNTRY, SCOPE_CUSTOMER),
        (None, ANY_COUNTRY, SCOPE_ORG),
    ):
        if candidate_country == ANY_COUNTRY and scope == SCOPE_CUSTOMER_COUNTRY:
            # A caller asking for "any country" would otherwise probe the
            # customer-default rung twice; skip the duplicate read.
            continue
        row = await get_rate_row(db, org_id, entity_id=candidate_entity, country=candidate_country)
        if row is not None:
            return FeeRate(fee_pct=q2(row.fee_pct), fee_min=q2(row.fee_min), scope=scope)

    raise ConflictError(
        "No service fee rate is configured for this client"
        f"{f' in {country}' if country else ''} — set a per-client rate or the "
        "organisation's standard rate before filing. The fee a client is charged "
        "is never defaulted.",
        code="fee_rate_not_configured",
    )


async def set_rate(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str | None = None,
    country: str | None = None,
    fee_pct: Decimal,
    fee_min: Decimal,
) -> VatFeeRate:
    """Type the contingency rate for one rung — the ONLY writer of `VatFeeRate`
    alongside `remove_rate`. `entity_id=None` writes the ORG STANDARD (R40's
    *"Standard fee is admin-editable"*); `country=None` writes the customer
    default; both together write C11's per-(customer, country) override.

    Gate order, fails CLOSED: module entitlement -> an org-level rung may carry
    no country (neither C11 nor R40 describes one) -> the entity exists in this
    org (opaque 404) -> the percentage and minimum validate.

    Idempotent: a repeat call with identical values returns the existing row and
    audits nothing (the `excise.set_rate` / `contract_audit.set_term` no-op
    convention). Any real change audits old->new IN THE SAME transaction as the
    mutation (§4.16) — this pair decides what a client is billed, so a change to
    it always carries an actor.

    Changing a rate NEVER re-rates a filed claim: a submitted claim carries its
    own frozen copy and this table is not read again (C10 — *"% / minimum
    changes only affect un-submitted declarations"*, R13's acceptance line).
    """
    await _require_module(db, org_id)

    country_value = _normalise_country(country)
    if entity_id is None and country_value != ANY_COUNTRY:
        raise ValidationError(
            "The organisation's standard rate applies to every refund country — "
            "a per-country rate needs a client",
            code="org_rate_has_no_country",
        )
    if entity_id is not None:
        await _require_entity(db, org_id, entity_id)

    pct = _validate_pct(fee_pct)
    minimum = _validate_min(fee_min)
    scope = (
        SCOPE_ORG
        if entity_id is None
        else (SCOPE_CUSTOMER if country_value == ANY_COUNTRY else SCOPE_CUSTOMER_COUNTRY)
    )

    existing = await get_rate_row(db, org_id, entity_id=entity_id, country=country_value)
    if existing is not None:
        old_pct, old_min = q2(existing.fee_pct), q2(existing.fee_min)
        if (old_pct, old_min) == (pct, minimum):
            return existing
        existing.fee_pct = pct
        existing.fee_min = minimum
        await db.flush()
        await audit_svc.record(
            db,
            audit_svc.A.TRANSPORT_FEE_RATE_SET,
            target_type="vat_fee_rate",
            target_id=existing.id,
            meta={
                "scope": scope,
                "entity_id": entity_id,
                "country": country_value,
                "old_fee_pct": str(old_pct),
                "new_fee_pct": str(pct),
                "old_fee_min": str(old_min),
                "new_fee_min": str(minimum),
            },
            org_id=org_id,  # explicit — callable outside an HTTP request context.
        )
        return existing

    row = VatFeeRate(
        org_id=org_id,
        entity_id=entity_id,
        country=country_value,
        fee_pct=pct,
        fee_min=minimum,
    )
    db.add(row)
    await db.flush()
    await audit_svc.record(
        db,
        audit_svc.A.TRANSPORT_FEE_RATE_SET,
        target_type="vat_fee_rate",
        target_id=row.id,
        meta={
            "scope": scope,
            "entity_id": entity_id,
            "country": country_value,
            "old_fee_pct": None,
            "new_fee_pct": str(pct),
            "old_fee_min": None,
            "new_fee_min": str(minimum),
        },
        org_id=org_id,
    )
    return row


async def remove_rate(
    db: AsyncSession, org_id: str, *, entity_id: str | None = None, country: str | None = None
) -> bool:
    """Drop one rung so resolution falls through to the next — and, if it was the
    last one, to the refusal. Returns `True` when a row was removed.

    Removing a rate changes NOTHING about any claim already filed under it: the
    claim carries its own frozen `fee_pct`/`fee_min`/`fee_eur` and this table is
    never consulted for it again (C10). That is the property that makes deleting
    a rate a safe administrative act rather than a rewrite of history.
    """
    await _require_module(db, org_id)
    country_value = _normalise_country(country)

    row = await get_rate_row(db, org_id, entity_id=entity_id, country=country_value)
    if row is None:
        return False

    old_pct, old_min = q2(row.fee_pct), q2(row.fee_min)
    row_id = row.id
    await db.delete(row)
    await db.flush()
    await audit_svc.record(
        db,
        audit_svc.A.TRANSPORT_FEE_RATE_REMOVE,
        target_type="vat_fee_rate",
        target_id=row_id,
        meta={
            "entity_id": entity_id,
            "country": country_value,
            "old_fee_pct": str(old_pct),
            "new_fee_pct": None,
            "old_fee_min": str(old_min),
            "new_fee_min": None,
        },
        org_id=org_id,
    )
    return True


def freeze_fee(claim: VatRefundClaim, rate: FeeRate) -> tuple[Decimal, str]:
    """C10's second half: stamp `fee_pct`/`fee_min`/`fee_eur` onto `claim` from
    the ALREADY-FROZEN VAT base. Returns `(fee_eur, basis)`.

    Pure and synchronous — no query, no flush. The caller (`lock.submit_claim`)
    owns the flush, so the fee lands in the same one as the frozen lines, the
    lock inserts and the status flip: a lost lock race rolls the fee back with
    everything else (see `freeze.py`'s own atomicity argument).

    Refuses rather than guesses, twice:

    * `vat_base_not_frozen` if `claim.vat_eur` is still NULL. A fee computed off
      an unfrozen base is exactly the drift R13 forbids — the base must already
      be the sum over exactly the locked claim set (C10's first bullet), never a
      period total and never a live recomputation.
    * `fee_already_frozen` if any of the three columns is already set. C10 says
      *"first entry to a locking state"*; re-freezing would silently re-rate a
      filed claim, which R13's acceptance line forbids in as many words. The
      lifecycle already makes this unreachable (`submit_claim` accepts only a
      `draft` claim, and a withdrawn claim can never return to `draft`), so this
      is the structural backstop for a future transition that forgets.
    """
    if claim.vat_eur is None:
        raise ConflictError(
            "The claim's VAT base is not frozen — a fee cannot be computed before it",
            code="vat_base_not_frozen",
        )
    if claim.fee_pct is not None or claim.fee_min is not None or claim.fee_eur is not None:
        raise ConflictError(
            "This claim's fee is already frozen — a filed claim is never re-rated",
            code="fee_already_frozen",
        )

    fee_eur, basis = compute_fee(claim.vat_eur, rate.fee_pct, rate.fee_min)
    claim.fee_pct = rate.fee_pct
    claim.fee_min = rate.fee_min
    claim.fee_eur = fee_eur
    return fee_eur, basis


__all__ = [
    "BASIS_MINIMUM",
    "BASIS_PERCENT",
    "SCOPE_CUSTOMER",
    "SCOPE_CUSTOMER_COUNTRY",
    "SCOPE_ORG",
    "FeeRate",
    "compute_fee",
    "freeze_fee",
    "get_rate_row",
    "list_rates",
    "remove_rate",
    "resolve_fee_rate",
    "set_rate",
]


# --------------------------------------------------------------------------- #
# Standard pricing vs a negotiated client price
# --------------------------------------------------------------------------- #
# The commercial model (owner decision, 2026-08-12): there is a STANDARD price,
# and an individual client is negotiated to a price expressed as a DISCOUNT from
# it. Four choices shape the code below, and each was taken deliberately:
#
# 1. The negotiated price is stored as an ABSOLUTE `(fee_pct, fee_min)` on the
#    client rung — never as a discount percentage. A stored discount would be a
#    live reference to the standard, so raising the standard would silently
#    re-rate every negotiated client, including ones with a signed contract
#    saying otherwise. Storing absolutes makes grandfathering the default and
#    re-rating a deliberate, audited act. Nothing new is stored for this: the
#    rate table already holds absolutes, so there is no migration.
#
# 2. The discount is therefore DERIVED for display — a fact computed by
#    comparing two rows, never a fact of record.
#
# 3. A discount applies to BOTH the percentage and the minimum. "20% off"
#    means 12% and €40, not 12% and an unchanged €50 floor.
#
# 4. A negotiated price may be ABOVE standard (a premium for difficult work), so
#    the derived discount can legitimately be negative. `kind` names which it is
#    rather than leaving a reader to work out the sign.

STANDARD = "standard"
DISCOUNT = "discount"
PREMIUM = "premium"
NO_STANDARD = "no_standard"


@dataclass(frozen=True)
class RateComparison:
    """A client's price set against the standard, for display and for a quote.

    `pct_discount` / `min_discount` are percentages OFF the standard: 20 means
    20% cheaper, -10 means 10% dearer. They are `None` when there is no standard
    to compare against, because "no discount" and "nothing to compare with" are
    different statements and a zero would blur them.
    """

    kind: str
    fee_pct: Decimal
    fee_min: Decimal
    standard_pct: Decimal | None
    standard_min: Decimal | None
    pct_discount: Decimal | None
    min_discount: Decimal | None


def _discount_between(negotiated: Decimal, standard: Decimal) -> Decimal | None:
    """How far below the standard a figure sits, as a percentage of it.

    `None` when the standard is zero: nothing is a meaningful percentage of
    zero, and returning 0 or 100 there would both be inventions.
    """
    if standard == 0:
        return None
    return q2((standard - negotiated) / standard * _HUNDRED)


def apply_discount(
    standard_pct: Decimal, standard_min: Decimal, discount_pct: Decimal
) -> tuple[Decimal, Decimal]:
    """Standard price minus a discount, applied to BOTH figures.

    A negative discount is a premium and is allowed. The result is clamped only
    by the same validators any typed rate passes, so a discount cannot produce a
    percentage outside 0..100 or a negative minimum by arithmetic when it could
    not be typed directly.
    """
    factor = (_HUNDRED - Decimal(discount_pct)) / _HUNDRED
    return _validate_pct(q2(standard_pct * factor)), _validate_min(q2(standard_min * factor))


async def standard_rate(db: AsyncSession, org_id: str) -> VatFeeRate | None:
    """The ORG STANDARD rung, or `None` when none is configured."""
    await _require_module(db, org_id)
    return await get_rate_row(db, org_id, entity_id=None, country=ANY_COUNTRY)


def compare_rate(fee_pct: Decimal, fee_min: Decimal, standard: VatFeeRate | None) -> RateComparison:
    """Set one price against the standard. Pure — no I/O, no rounding of the
    inputs, and it never decides anything: it only reports the relationship."""
    if standard is None:
        return RateComparison(NO_STANDARD, fee_pct, fee_min, None, None, None, None)

    std_pct, std_min = q2(standard.fee_pct), q2(standard.fee_min)
    pct_off = _discount_between(q2(fee_pct), std_pct)
    min_off = _discount_between(q2(fee_min), std_min)

    if q2(fee_pct) == std_pct and q2(fee_min) == std_min:
        kind = STANDARD
    elif q2(fee_pct) > std_pct or (q2(fee_pct) == std_pct and q2(fee_min) > std_min):
        kind = PREMIUM
    else:
        kind = DISCOUNT
    return RateComparison(kind, q2(fee_pct), q2(fee_min), std_pct, std_min, pct_off, min_off)


async def set_rate_by_discount(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    country: str | None = None,
    discount_pct: Decimal,
) -> VatFeeRate:
    """Negotiate a client off the STANDARD by a discount, applied to both the
    percentage and the minimum.

    The discount is a way of ARRIVING at the numbers, not a thing that is kept:
    the standard is read once, the two figures are computed, and `set_rate`
    stores them as absolutes. So the client's price is captured at the moment it
    was agreed and a later change to the standard leaves it alone — which is the
    behaviour a signed rate card implies, and the reason a stored discount was
    rejected.

    Refuses when no standard exists: a discount off nothing is not a price, and
    inventing a base here would be the same invention `resolve_fee_rate` refuses
    to make. Refuses for the org rung too — the standard cannot be a discount
    off itself.
    """
    if entity_id is None:
        raise ValidationError(
            "The standard rate cannot be a discount off itself — set it directly",
            code="discount_needs_a_client",
        )
    standard = await standard_rate(db, org_id)
    if standard is None:
        raise ValidationError(
            "No standard rate is configured, so there is nothing to discount from",
            code="no_standard_rate",
        )
    pct, minimum = apply_discount(q2(standard.fee_pct), q2(standard.fee_min), discount_pct)
    return await set_rate(
        db, org_id, entity_id=entity_id, country=country, fee_pct=pct, fee_min=minimum
    )
