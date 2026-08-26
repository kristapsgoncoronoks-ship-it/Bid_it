"""The supplier overcharge CLAIM-BACK lifecycle (G4.5; R41; `BA_fleet_fuel.md`
§2.4, §4.5).

R41, verbatim: *"**Supplier overcharge claim-back**: a per-(supplier × period)
lifecycle `detected → packaged → claimed → recovered | rejected | written_off`,
with an Excel evidence packet and a formal PDF claim letter (with a credit/refund
demand and a deadline) built from the SAME line source, and a `recovered_total`
that feeds the north star. Read-only over the analytics."*

WHAT THIS MODULE IS, AND WHAT IT IS NOT
-----------------------------------------
Detection lives in `contract_audit.py` and writes nothing. THIS module is the
worklist: the small, deliberately-driven state machine that turns a detected
€-exposure into a chase, and — when the supplier actually credits it — into the
booked cash §2.4 names the north star (*"`recovered_total()` = the booked-cash
north star"*). It imports `contract_audit`; the reverse import does not exist,
which is what keeps "read-only over the analytics" structural.

It touches NOTHING in the VAT engine. No claim, no claim line, no invoice lock,
no freeze, no checklist, no close stage, no figure on any of them. §3.L's
advisory covenant over this analysis family is honoured by construction: the
only rows this module writes are its own.

THE EDGE SET IS EXACTLY THE HARVESTED CHAIN
---------------------------------------------
`TRANSITIONS` draws §4.5's chain literally: `detected → packaged → claimed →
{recovered, rejected, written_off}`, with those three terminal. No shortcut edge
is invented — writing a claim off straight from `detected` is not a harvested
move, and §10 forbids inventing one. (The WO-73 precedent: `inactive` is
terminal because no re-onboarding edge was harvested; the owner can add an edge
as a one-line decision, which is better than guessing it now.)

An illegal edge refuses 409 `overcharge_transition_invalid` with NOTHING
mutated — no status change, no amount, no note, no audit row. A test asserts the
row is column-for-column identical afterwards.

THE FROZEN FIGURE, AND WHY IT IS FROZEN
-----------------------------------------
`open_claim` runs `contract_audit.audit(period, supplier)` and SNAPSHOTS the
euro it found into `detected_eur` (with `lines_count` beside it). It is never
re-derived afterwards. This is the same reasoning that freezes a VAT claim's
base at submission (G2.5 / ADR-P3): the figure an operator has quoted to a
supplier in a demand letter must not move underneath them because a later
re-ingest, a corrected line or an edited contract term changed the arithmetic.
The live figure is always available — that is what `contract_audit.audit` is
for — and the two disagreeing is information, not a bug.

`open_claim` is idempotent on the natural key `(org, supplier, period)`: a
second call returns the existing claim-back UNCHANGED rather than re-snapshotting
it, for exactly the same reason.

WHY OPENING A CLAIM-BACK WITH NOTHING DETECTED IS REFUSED
-----------------------------------------------------------
`no_overcharge_detected` (422). A claim-back at €0 is a letter demanding
nothing; it would also put a zero-euro row into the denominator of every
"how many of our claim-backs got paid?" question an operator will ever ask.
Fails CLOSED — the operator is told the audit found nothing, which is a real and
useful answer, rather than being handed an empty worklist item.

MONEY (§4.9, §4.14, §4.10)
---------------------------
Both euros are `Decimal`, quantized through `app.core.money.q2`, stored
`Numeric(14, 2)`, and both are EUR by construction (they derive from the EUR
price columns — see `contract_audit.py`'s §4.14 section for the full argument).
Nothing here sums across currencies because nothing here is expressed in a
document currency at all.

`detected_eur` is SERVER-COMPUTED (§4.10): a caller cannot supply it. The one
client-supplied figure is `recovered_eur` on the `recovered` transition — the
externally-observed cash — and it is bounded to `(0, detected_eur]`. That bound
is not a harvested rule; it is master-context §4 invariant 13 (no
over-crediting) applied to this ledger: a north-star total unbounded by its own
evidence is not a measurement. A settlement genuinely larger than the demand is
a different, negotiated agreement and should be recorded as such, not silently
inflate the KPI.

TWO EUROS, TWO NAMES, NEVER CONFLATED
---------------------------------------
`contract_audit.audit(...).recover_eur` is the €-exposure DETECTED.
`recovered_total()` is the € actually BOOKED BACK. §2.4's north-star figure —
the one `recovery.recovery_dashboard` reports as `overcharges_eur` — is the
SECOND one. Reporting the first under that name would claim money nobody has
received, which is the same class of error R52 forbids for the two overpay
grains ("label them distinctly").
"""

from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionError, ValidationError
from app.core.money import q2
from app.models.transport.overcharge import OVERCHARGE_STATES, VatOverchargeClaim
from app.services import audit as audit_svc
from app.services import modules
from app.services.transport import contract_audit

# `BA_fleet_fuel.md` §4.5 / R41, drawn literally, PLUS the owner-decided §12
# edges (2026-08-08): a `detected` or `packaged` claim-back can be explicitly
# IGNORED (audited, reason required — "not a silent dead end"), and an ignored
# one can be reconsidered back to `detected` (documented interpretation: "we
# never asked" is reversible right up until a demand is actually sent). The
# harvested `claimed` outcomes stay exactly the three the spec draws — once a
# demand went out, only the counterparty's answer moves it. A state whose
# tuple is empty is TERMINAL. `contract_audit` is not consulted after
# `detected` — the figure is frozen at that point (see the module docstring).
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "detected": ("packaged", "ignored"),
    "packaged": ("claimed", "ignored"),
    "claimed": ("recovered", "rejected", "written_off"),
    "recovered": (),
    "rejected": (),
    "written_off": (),
    "ignored": ("detected",),
}

# The state a claim-back is opened in — the first link of the harvested chain.
INITIAL_STATE = "detected"
# The one state that books cash into the north-star total.
RECOVERED_STATE = "recovered"

_YEAR_RE = re.compile(r"^\d{4}$")


async def _require_module(db: AsyncSession, org_id: str) -> None:
    """`modules.is_enabled`, never `require_enabled` — a service signals with
    `AppError`, not `fastapi.HTTPException` (ADR-P3 rule 3, fails CLOSED)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


async def get_claim(db: AsyncSession, org_id: str, claim_id: str) -> VatOverchargeClaim:
    """One claim-back by id, ORG-SCOPED. A cross-tenant id is indistinguishable
    from an unknown one: 404 `overcharge_claim_not_found`, never 403
    (master-context §4.4 — object-id guessing must yield zero information)."""
    await _require_module(db, org_id)
    row = await db.scalar(
        select(VatOverchargeClaim).where(
            VatOverchargeClaim.org_id == org_id, VatOverchargeClaim.id == claim_id
        )
    )
    if row is None:
        raise NotFoundError("Overcharge claim not found", code="overcharge_claim_not_found")
    return row


async def list_claims(
    db: AsyncSession,
    org_id: str,
    *,
    period: str | None = None,
    supplier: str | None = None,
    status: str | None = None,
) -> list[VatOverchargeClaim]:
    """The claim-back worklist, org-scoped. `status` is validated against the
    harvested six so a typo reads as a refusal rather than as an empty
    worklist (the same fail-CLOSED reasoning `validate_period` carries)."""
    await _require_module(db, org_id)
    where = [VatOverchargeClaim.org_id == org_id]
    if period:
        contract_audit.validate_period(period)
        where.append(VatOverchargeClaim.period == period)
    if supplier:
        where.append(VatOverchargeClaim.supplier == supplier)
    if status:
        _validate_status(status)
        where.append(VatOverchargeClaim.status == status)
    rows = await db.scalars(
        select(VatOverchargeClaim)
        .where(*where)
        .order_by(
            VatOverchargeClaim.period.desc(),
            VatOverchargeClaim.supplier,
        )
    )
    return list(rows)


def _validate_status(status: str) -> None:
    if status not in OVERCHARGE_STATES:
        raise ValidationError(
            f"'{status}' is not a claim-back state — use one of: " + ", ".join(OVERCHARGE_STATES),
            code="invalid_overcharge_status",
        )


async def open_claim(
    db: AsyncSession, org_id: str, *, supplier: str, period: str
) -> VatOverchargeClaim:
    """Open the claim-back for one (supplier, period) at `detected`, snapshotting
    what `contract_audit.audit` found.

    Idempotent on the natural key: an existing claim-back is returned UNCHANGED
    and audits nothing — re-opening must never re-snapshot a figure an operator
    has already quoted to a supplier (see the module docstring).

    Refuses 422 `no_overcharge_detected` when the audit found no breach, so the
    worklist can never carry a €0 demand.
    """
    await _require_module(db, org_id)
    contract_audit.validate_period(period)

    existing = await db.scalar(
        select(VatOverchargeClaim).where(
            VatOverchargeClaim.org_id == org_id,
            VatOverchargeClaim.supplier == supplier,
            VatOverchargeClaim.period == period,
        )
    )
    if existing is not None:
        return existing

    result = await contract_audit.audit(db, org_id, period=period, supplier=supplier)
    if not result.breaches or result.recover_eur <= 0:
        raise ValidationError(
            f"No contract breach was detected for {supplier} in {period} — "
            "there is nothing to claim back",
            code="no_overcharge_detected",
        )

    row = VatOverchargeClaim(
        org_id=org_id,
        supplier=supplier,
        period=period,
        status=INITIAL_STATE,
        detected_eur=q2(result.recover_eur),
        lines_count=len(result.breaches),
    )
    db.add(row)
    await db.flush()
    await audit_svc.record(
        db,
        audit_svc.A.TRANSPORT_OVERCHARGE_OPEN,
        target_type="vat_overcharge_claim",
        target_id=row.id,
        meta={
            "supplier": supplier,
            "period": period,
            "old_status": None,
            "new_status": INITIAL_STATE,
            "detected_eur": str(row.detected_eur),
            "lines_count": row.lines_count,
        },
        org_id=org_id,
    )
    return row


async def advance_claim(
    db: AsyncSession,
    org_id: str,
    claim_id: str,
    *,
    to_status: str,
    recovered_eur: Decimal | None = None,
    note: str | None = None,
) -> VatOverchargeClaim:
    """Move one claim-back along the harvested chain — the ONLY writer of
    `status` and `recovered_eur`.

    Gate order, all fail-CLOSED, and NOTHING is mutated unless every gate passes:
    1. the `transport` module entitlement (ADR-P3 rule 3);
    2. the claim-back, fetched org-scoped (opaque 404);
    3. `to_status` is one of the harvested six (422 `invalid_overcharge_status`);
    4. the edge `current → to_status` exists in `TRANSITIONS` (409
       `overcharge_transition_invalid`) — which also covers "a terminal state
       accepts nothing further", since its tuple is empty;
    5. `recovered` requires a booked amount in `(0, detected_eur]` (422
       `recovered_amount_required` / `recovered_amount_invalid`), and no other
       target state accepts one (422 `recovered_amount_not_applicable`) — a
       rejected claim-back that carried a euro would corrupt the north star.

    The mutation and its audit row (old→new) are added to the SAME session, so
    the caller's commit persists them atomically (§4.16).
    """
    await _require_module(db, org_id)
    row = await get_claim(db, org_id, claim_id)
    _validate_status(to_status)

    allowed = TRANSITIONS[row.status]
    if to_status not in allowed:
        legal = ", ".join(allowed) if allowed else "nothing — it is a final state"
        raise ConflictError(
            f"A '{row.status}' claim-back cannot move to '{to_status}'; from here it can go to: "
            f"{legal}",
            code="overcharge_transition_invalid",
        )

    # §12: ignoring is explicit and REASONED — the audit trail is the point.
    if to_status == "ignored" and not (note or "").strip():
        raise ValidationError(
            "Ignoring a claim-back needs a reason (why this one is not being pursued)",
            code="ignore_reason_required",
        )

    booked: Decimal | None = None
    if to_status == RECOVERED_STATE:
        if recovered_eur is None:
            raise ValidationError(
                "Recording a recovery needs the amount the supplier actually credited (EUR)",
                code="recovered_amount_required",
            )
        booked = q2(recovered_eur)
        if booked <= 0 or booked > Decimal(row.detected_eur):
            raise ValidationError(
                f"The recovered amount must be greater than 0 and at most the detected "
                f"{row.detected_eur} EUR",
                code="recovered_amount_invalid",
            )
    elif recovered_eur is not None:
        raise ValidationError(
            f"A '{to_status}' claim-back books no cash — only a recovery carries an amount",
            code="recovered_amount_not_applicable",
        )

    old_status = row.status
    old_note = row.note
    row.status = to_status
    if booked is not None:
        row.recovered_eur = booked
    if note is not None:
        row.note = note
    await db.flush()
    await audit_svc.record(
        db,
        audit_svc.A.TRANSPORT_OVERCHARGE_TRANSITION,
        target_type="vat_overcharge_claim",
        target_id=row.id,
        meta={
            "supplier": row.supplier,
            "period": row.period,
            "old_status": old_status,
            "new_status": to_status,
            "old_note": old_note,
            "new_note": row.note,
            "recovered_eur": None if booked is None else str(booked),
        },
        org_id=org_id,
    )
    return row


async def recovered_total(db: AsyncSession, org_id: str, *, year: int | None = None) -> Decimal:
    """§2.4's booked-cash north star: Σ `recovered_eur` over the claim-backs that
    actually reached `recovered`, org-scoped, in EUR.

    `year` filters on the claim-back's own `period` (`"YYYY-MM"`), so the figure
    lines up with the refund year `recovery.recovery_dashboard` reports. Never
    raises on an empty set: a year with no recovery is `0.00`, which is the true
    answer ("nothing has come back yet") rather than an absence.

    A `rejected` or `written_off` claim-back contributes nothing by construction
    — the filter is on the state, not on the column, so a stray amount could not
    leak into the total even if one were ever stored beside another state (which
    `advance_claim` refuses).
    """
    await _require_module(db, org_id)
    where = [
        VatOverchargeClaim.org_id == org_id,
        VatOverchargeClaim.status == RECOVERED_STATE,
    ]
    if year is not None:
        if not _YEAR_RE.match(str(year)):
            raise ValidationError(
                f"'{year}' is not a valid year — use a four-digit year", code="invalid_year"
            )
        where.append(VatOverchargeClaim.period.like(f"{year}-%"))
    total = await db.scalar(select(func.sum(VatOverchargeClaim.recovered_eur)).where(*where))
    return q2(Decimal(total)) if total is not None else Decimal("0.00")


__all__ = [
    "INITIAL_STATE",
    "OVERCHARGE_STATES",
    "RECOVERED_STATE",
    "TRANSITIONS",
    "advance_claim",
    "get_claim",
    "list_claims",
    "open_claim",
    "recovered_total",
]
