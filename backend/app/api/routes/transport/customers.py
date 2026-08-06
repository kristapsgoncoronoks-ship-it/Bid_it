"""Transport routes slice 2 — the CUSTOMER lifecycle and per-country
activation ladder over HTTP (WO-77; the WO-73 / R44 services).

WHY THESE ROUTES EXIST AT ALL
-----------------------------
`customer_lifecycle.py`'s own module docstring records the gap this closes
verbatim: *"Role enforcement is structural at the (future) route layer …
no `api/routes/transport/*` route exists yet, so the admin-click requirement
lands as the documented intent for that route's permission."* Every
transition here is that explicit admin click — never a side effect of
ingestion, claim building or the close — and each is audited old→new by the
service (§4.16).

Thin controllers ONLY (engineering-rules §3), the WO-76 pattern: parse →
structural permission gate → call the already-gated service → shape the
response. Refusals are the SERVICE's own (`not_a_prospect`,
`lifecycle_transition_invalid`, `invalid_country`, `country_not_requested`,
`country_transition_invalid`, `entity_not_found`, `module_not_enabled`) and
reach the client through the one `app.main` handler (§4.20).

AUTHORIZATION: router-level `VAT_READ`; every transition overrides to
`VAT_WRITE`. Activating a customer does not submit or lock anything — it
governs whether a FUTURE submission may proceed (the R44 gate reads this
state inside `lock.submit_claim`, which is itself VAT_SUBMIT-gated). No new
permission member (§10).

THE GATE STAYS IN THE SERVICE: these routes only move state; the fail-CLOSED
`enforce_activation` predicate is untouched and still runs inside
`submit_claim`. Reading the lifecycle blocks nothing and changes nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.transport.customer_lifecycle import VatCountryActivation, VatCustomerLifecycle
from app.schemas.transport_admin import (
    ActivationIn,
    CountryActivationOut,
    LifecycleOut,
)
from app.services.transport import customer_lifecycle as lifecycle_svc

# Structural authorization (ADR-0024): router-level VAT_READ; every
# transition declares VAT_WRITE per-route below.
router = APIRouter(
    prefix="/transport/customers",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.VAT_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.VAT_WRITE))]


def _country_out(row: VatCountryActivation) -> CountryActivationOut:
    return CountryActivationOut(id=row.id, country=row.country, status=row.status)


def _lifecycle_out(
    entity_id: str,
    lifecycle: VatCustomerLifecycle | None,
    countries: list[VatCountryActivation],
) -> LifecycleOut:
    return LifecycleOut(
        entity_id=entity_id,
        id=lifecycle.id if lifecycle is not None else None,
        status=lifecycle.status if lifecycle is not None else None,
        countries=[_country_out(c) for c in countries],
    )


async def _overview(db, org_id: str, entity_id: str) -> LifecycleOut:
    lifecycle, countries = await lifecycle_svc.lifecycle_overview(db, org_id, entity_id)
    return _lifecycle_out(entity_id, lifecycle, countries)


@router.get("/{entity_id}/lifecycle", response_model=LifecycleOut)
async def get_lifecycle(entity_id: str, current: CurrentUser, db: DbSession):
    """The customer's lifecycle status + every country-activation row. A
    `null` status means "never onboarded" — a meaningful absence the R44 gate
    treats exactly like not-active, so it is reported, not 404'd; the 404
    keys off the ENTITY (§4.4, opaque cross-tenant)."""
    return await _overview(db, current.org_id, entity_id)


@router.post("/{entity_id}/prospect", response_model=LifecycleOut, dependencies=_WRITE)
async def add_prospect(entity_id: str, current: CurrentUser, db: DbSession):
    """`(none) → prospect` (F1) — idempotent, and it NEVER downgrades a real
    client of any status: an existing row is returned unchanged and audits
    nothing."""
    await lifecycle_svc.add_prospect(db, current.org_id, entity_id)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post("/{entity_id}/promote", response_model=LifecycleOut, dependencies=_WRITE)
async def promote_prospect(entity_id: str, current: CurrentUser, db: DbSession):
    """`prospect → pending` — the onboarding handoff. Any other state refuses
    409 `not_a_prospect`: promotion and activation are separate deliberate
    steps, never skippable."""
    await lifecycle_svc.promote_prospect(db, current.org_id, entity_id)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post("/{entity_id}/activation", response_model=LifecycleOut, dependencies=_WRITE)
async def set_activation(entity_id: str, body: ActivationIn, current: CurrentUser, db: DbSession):
    """F1's `pending ↔ active` toggle — the explicit admin click that opens
    (or closes) the customer to submissions. An illegal edge (from prospect,
    from inactive, or an idempotent repeat) refuses 409
    `lifecycle_transition_invalid`."""
    await lifecycle_svc.set_activation(db, current.org_id, entity_id, body.active)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post("/{entity_id}/inactive", response_model=LifecycleOut, dependencies=_WRITE)
async def set_inactive(entity_id: str, current: CurrentUser, db: DbSession):
    """`active → inactive` — the churn edge / commercial off-switch: the R44
    gate refuses the next submission the moment this lands, while every
    historical claim is untouched. Terminal in this slice (no re-onboarding
    edge is harvested — recorded in the service, never invented here)."""
    await lifecycle_svc.set_inactive(db, current.org_id, entity_id)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post(
    "/{entity_id}/countries/{country}/request", response_model=LifecycleOut, dependencies=_WRITE
)
async def request_country(entity_id: str, country: str, current: CurrentUser, db: DbSession):
    """`(none) → requested` — the first half of the linear country ladder
    (request and receive the power of attorney). Idempotent and never a
    downgrade; a malformed code refuses 422 `invalid_country`."""
    await lifecycle_svc.request_country(db, current.org_id, entity_id, country)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post(
    "/{entity_id}/countries/{country}/activation",
    response_model=LifecycleOut,
    dependencies=_WRITE,
)
async def set_country_activation(
    entity_id: str, country: str, body: ActivationIn, current: CurrentUser, db: DbSession
):
    """`requested ↔ active` (F3's explicit admin click). Activating a country
    that was never requested refuses 409 `country_not_requested` — the
    harvested ladder is linear, and the request step is where the country's
    document set gets gathered; any other illegal edge is 409
    `country_transition_invalid`."""
    await lifecycle_svc.set_country_activation(db, current.org_id, entity_id, country, body.active)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)
