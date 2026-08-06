"""The customer lifecycle + per-country activation service (G2.11;
`BA_fleet_fuel.md` §3.F F1/F3, §3.E "Activation gates layered on top",
§4's state tables, R44).

WHAT THIS MODULE IS
-------------------
The writer half of the two G2.11 tables (`app/models/transport/
customer_lifecycle.py` — see that docstring for why they are transport-
local tables keyed to `issuer_profiles`, never columns on it) plus the ONE
gate predicate `enforce_activation` that `lock.submit_claim` calls (the
`is_synthetic()` centralization discipline: every future surface that must
ask "may we legally act for this customer in this country?" imports THIS
function, never re-derives the check).

THE TRANSITION SET IS EXACTLY THE HARVESTED ONE
-----------------------------------------------
Customer (F1 + §4's state table): `(none) → prospect` (`add_prospect`,
idempotent, NEVER downgrades a real client of any status), `prospect →
pending` (`promote_prospect`, "the onboarding handoff"), `pending ↔
active` (`set_activation`, F1's own toggle), `active → inactive`
(`set_inactive`, §4's churn edge). `inactive` is TERMINAL in this slice —
no re-onboarding edge is harvested, and inventing one is master-context
§10 territory; if the owner wants a returning-client path it is a
one-line future decision, recorded here rather than guessed.
Refund country (§4: "(none) → requested → active", F3): `request_country`
((none) → requested, idempotent, never downgrades) and
`set_country_activation` (requested ↔ active). Activating a
never-requested country is refused — the harvested order is linear, each
step an explicit admin click (F3: "activation stays an explicit admin
click"; `country_ready_to_activate` — F3's informational helper — is
deferred with `country_requirements` to the customer-document-store slice
WO-60 already named, NOT silently skipped).

WHO MAY MOVE A STATE
--------------------
Every transition is an explicit operator call; no transition ever happens
as a side effect of ingestion, claim building, or the close. Role
enforcement is structural at the (future) route layer — the same posture
as WO-56's `override_minimum` boolean: no `api/routes/transport/*` route
exists yet, so the admin-click requirement lands as the documented intent
for that route's permission, and every real state change is audited
old→new HERE (§4.16) so the actor trail exists from day one.

WHY THE GATE FAILS CLOSED (absent row = not active)
---------------------------------------------------
F1: "EVERY legal/claim gate keys off `status == 'active'` — a prospect is
ignored exactly like a pending customer." An entity with NO lifecycle row
was never onboarded at all; letting it submit would make the unonboarded
strictly more privileged than the onboarded-but-pending, inverting the
rule. Same for the country half: only an `active` country-activation row
passes; `requested` (documents being gathered) and absence both refuse.
One harvested nuance documented rather than reproduced: Fleet Fuel's
`set_status_code` passed `gate_activation=False` ("the adjustable
checklist supersedes the coarse activation flags", §3.E) — but R44's own
acceptance line ("submitting a claim for a PROSPECT is refused with the
ACTIVATION message") is the binding requirement, the checklist here
(WO-60, a partial harvest) checks data completeness and never the status
flag, and this codebase has exactly ONE submission path, so the gate is
unconditional in `submit_claim` — fail-toward-blocking.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionError, ValidationError
from app.models.transport.customer_lifecycle import (
    COUNTRY_STATES,
    CUSTOMER_STATES,
    VatCountryActivation,
    VatCustomerLifecycle,
)
from app.services import audit, issuer, modules

__all__ = [
    "CUSTOMER_STATES",
    "COUNTRY_STATES",
    "add_prospect",
    "promote_prospect",
    "set_activation",
    "set_inactive",
    "request_country",
    "set_country_activation",
    "get_lifecycle",
    "get_country_activation",
    "lifecycle_overview",
    "enforce_activation",
]


async def _require_module(db: AsyncSession, org_id: str) -> None:
    """`modules.is_enabled`, never `require_enabled` — a service signals
    with `AppError`, not `fastapi.HTTPException` (the invariant pattern
    every transport service follows)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


async def _require_entity(db: AsyncSession, org_id: str, entity_id: str) -> None:
    """Org-scoped entity resolution via the issuer SERVICE (ADR-P3 rule 2)
    — a cross-tenant entity id is indistinguishable from an unknown one
    (master-context §4.4: opaque 404, never 403)."""
    entity = await issuer.get_by_id(db, org_id, entity_id)
    if entity is None:
        raise NotFoundError("Entity not found", code="entity_not_found")


async def get_lifecycle(
    db: AsyncSession, org_id: str, entity_id: str
) -> VatCustomerLifecycle | None:
    """The (org, entity) lifecycle row, or None when the entity was never
    onboarded (which the gate treats exactly like not-active)."""
    return await db.scalar(
        select(VatCustomerLifecycle).where(
            VatCustomerLifecycle.org_id == org_id,
            VatCustomerLifecycle.entity_id == entity_id,
        )
    )


async def get_country_activation(
    db: AsyncSession, org_id: str, entity_id: str, country: str
) -> VatCountryActivation | None:
    return await db.scalar(
        select(VatCountryActivation).where(
            VatCountryActivation.org_id == org_id,
            VatCountryActivation.entity_id == entity_id,
            VatCountryActivation.country == country.upper(),
        )
    )


async def lifecycle_overview(
    db: AsyncSession, org_id: str, entity_id: str
) -> tuple[VatCustomerLifecycle | None, list[VatCountryActivation]]:
    """The entity's lifecycle row plus every country-activation row,
    ordered by country (WO-77 — the route-facing read). Module-gated +
    opaque-404 ENTITY resolution first (§4.4: a cross-tenant entity id is
    indistinguishable from an unknown one). A `None` lifecycle is a
    MEANINGFUL absence — "never onboarded", which the R44 gate treats
    exactly like not-active — so it is returned, never 404'd: the 404 keys
    off the ENTITY, never off lifecycle absence (a customer an operator
    still has to onboard must be visible in order to be onboarded)."""
    await _require_module(db, org_id)
    await _require_entity(db, org_id, entity_id)

    lifecycle = await get_lifecycle(db, org_id, entity_id)
    countries = list(
        await db.scalars(
            select(VatCountryActivation)
            .where(
                VatCountryActivation.org_id == org_id,
                VatCountryActivation.entity_id == entity_id,
            )
            .order_by(VatCountryActivation.country)
        )
    )
    return lifecycle, countries


async def _audit_lifecycle(
    db: AsyncSession,
    org_id: str,
    row: VatCustomerLifecycle,
    *,
    old_status: str | None,
    verb: str,
) -> None:
    await audit.record(
        db,
        audit.A.TRANSPORT_CUSTOMER_LIFECYCLE_SET,
        target_type="vat_customer_lifecycle",
        target_id=row.id,
        meta={
            "entity_id": row.entity_id,
            "old_status": old_status,
            "new_status": row.status,
            "verb": verb,
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )


async def add_prospect(db: AsyncSession, org_id: str, entity_id: str) -> VatCustomerLifecycle:
    """`(none) → prospect` — F1 verbatim: idempotent, and it NEVER
    downgrades a real client of any status. An existing row at ANY status
    is returned UNCHANGED and nothing is audited (the idempotent-no-op-
    audits-nothing convention). The harvested idempotency key was
    `company_name` because company name WAS Fleet Fuel's customer
    identity; here the customer identity IS the issuer-profile row, so the
    key is `(org, entity)` — the same rule, translated (see the model
    docstring / WO-73)."""
    await _require_module(db, org_id)
    await _require_entity(db, org_id, entity_id)

    existing = await get_lifecycle(db, org_id, entity_id)
    if existing is not None:
        return existing

    row = VatCustomerLifecycle(org_id=org_id, entity_id=entity_id, status="prospect")
    db.add(row)
    await db.flush()
    await _audit_lifecycle(db, org_id, row, old_status=None, verb="add_prospect")
    return row


async def _transition(
    db: AsyncSession,
    org_id: str,
    entity_id: str,
    *,
    expected_from: tuple[str, ...],
    to: str,
    verb: str,
    error_code: str,
) -> VatCustomerLifecycle:
    """The one edge-enforcing mutator behind every customer verb. A
    disallowed edge refuses with 409 naming both states; a missing row
    refuses the same way (there is nothing to move — `add_prospect` is the
    only creator)."""
    await _require_module(db, org_id)
    await _require_entity(db, org_id, entity_id)

    row = await get_lifecycle(db, org_id, entity_id)
    current = row.status if row is not None else None
    if row is None or current not in expected_from:
        state = current if current is not None else "not onboarded"
        raise ConflictError(
            f"Customer is '{state}' — cannot move to '{to}' "
            f"(allowed from: {', '.join(expected_from)})",
            code=error_code,
        )
    old = row.status
    row.status = to
    await db.flush()
    await _audit_lifecycle(db, org_id, row, old_status=old, verb=verb)
    return row


async def promote_prospect(db: AsyncSession, org_id: str, entity_id: str) -> VatCustomerLifecycle:
    """`prospect → pending` — F1's onboarding handoff. Refuses any other
    state (409 `not_a_prospect`): promotion and activation are separate
    deliberate steps, never skippable."""
    return await _transition(
        db,
        org_id,
        entity_id,
        expected_from=("prospect",),
        to="pending",
        verb="promote_prospect",
        error_code="not_a_prospect",
    )


async def set_activation(
    db: AsyncSession, org_id: str, entity_id: str, active: bool
) -> VatCustomerLifecycle:
    """F1 verbatim: "`set_activation` toggles pending ↔ active" — the
    explicit admin click. From `prospect` (promote first) or `inactive`
    (terminal in this slice) → 409 `lifecycle_transition_invalid`. An
    idempotent repeat (already in the target state) is likewise refused
    rather than silently re-audited — the toggle has exactly two harvested
    edges."""
    if active:
        return await _transition(
            db,
            org_id,
            entity_id,
            expected_from=("pending",),
            to="active",
            verb="set_activation",
            error_code="lifecycle_transition_invalid",
        )
    return await _transition(
        db,
        org_id,
        entity_id,
        expected_from=("active",),
        to="pending",
        verb="set_activation",
        error_code="lifecycle_transition_invalid",
    )


async def set_inactive(db: AsyncSession, org_id: str, entity_id: str) -> VatCustomerLifecycle:
    """`active → inactive` — §4's churn edge. The commercial off-switch:
    the gate refuses the next submission the moment this lands, without
    touching any historical claim."""
    return await _transition(
        db,
        org_id,
        entity_id,
        expected_from=("active",),
        to="inactive",
        verb="set_inactive",
        error_code="lifecycle_transition_invalid",
    )


def _validate_country(country: str) -> str:
    c = country.upper()
    if len(c) != 2 or not c.isalpha():
        raise ValidationError(
            f"'{country}' is not an ISO 3166-1 alpha-2 country code", code="invalid_country"
        )
    return c


async def request_country(
    db: AsyncSession, org_id: str, entity_id: str, country: str
) -> VatCountryActivation:
    """`(none) → requested` — the first half of §4's linear country ladder
    ("request and receive the country documents (power of attorney)",
    §3.E). Idempotent, and like `add_prospect` it never downgrades: an
    existing row at ANY status (including `active`) is returned UNCHANGED
    and audits nothing."""
    await _require_module(db, org_id)
    await _require_entity(db, org_id, entity_id)
    c = _validate_country(country)

    existing = await get_country_activation(db, org_id, entity_id, c)
    if existing is not None:
        return existing

    row = VatCountryActivation(org_id=org_id, entity_id=entity_id, country=c, status="requested")
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_COUNTRY_ACTIVATION_SET,
        target_type="vat_country_activation",
        target_id=row.id,
        meta={
            "entity_id": entity_id,
            "country": c,
            "old_status": None,
            "new_status": "requested",
            "verb": "request_country",
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return row


async def set_country_activation(
    db: AsyncSession, org_id: str, entity_id: str, country: str, active: bool
) -> VatCountryActivation:
    """`requested ↔ active` — the explicit admin click (F3). Activating a
    country that was never requested is refused (409
    `country_not_requested`): the harvested ladder is linear, and the
    request step is where the country's document set gets gathered."""
    await _require_module(db, org_id)
    await _require_entity(db, org_id, entity_id)
    c = _validate_country(country)

    row = await get_country_activation(db, org_id, entity_id, c)
    if row is None:
        raise ConflictError(
            f"Refund country {c} has not been requested for this customer — "
            "request the country documents first",
            code="country_not_requested",
        )
    target = "active" if active else "requested"
    expected = "requested" if active else "active"
    if row.status != expected:
        raise ConflictError(
            f"Refund country {c} is '{row.status}' — cannot move to '{target}'",
            code="country_transition_invalid",
        )
    old = row.status
    row.status = target
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_COUNTRY_ACTIVATION_SET,
        target_type="vat_country_activation",
        target_id=row.id,
        meta={
            "entity_id": entity_id,
            "country": c,
            "old_status": old,
            "new_status": target,
            "verb": "set_country_activation",
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return row


async def enforce_activation(
    db: AsyncSession, org_id: str, *, entity_id: str, refund_country: str
) -> None:
    """THE R44 gate — the one predicate every legal surface imports.
    Customer half first (§3.E's own bullet order — the more actionable
    message wins for a prospect in an unactivated country), then the
    country half. Fails CLOSED on absence in both halves (module
    docstring). Raises `ConflictError`; returns None when both pass.
    Deliberately audits nothing — it is a read; the outcome is visible on
    the caller's own audit trail."""
    lifecycle = await get_lifecycle(db, org_id, entity_id)
    if lifecycle is None or lifecycle.status != "active":
        state = lifecycle.status if lifecycle is not None else "not onboarded"
        raise ConflictError(
            f"Customer is '{state}', not 'active' — complete the activation "
            "(trade registry, bank account and signed contract) on the "
            "Customers page first",
            code="customer_not_active",
        )
    country = refund_country.upper()
    activation = await get_country_activation(db, org_id, entity_id, country)
    if activation is None or activation.status != "active":
        raise ConflictError(
            f"Refund country {country} is not activated for this customer — "
            "request and receive the country documents (power of attorney) first",
            code="country_not_activated",
        )
