from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.models.organization import Organization
from app.core.roles import is_admin_or_above
from app.schemas.tenancy import (
    BillingOut, CheckoutOut, CheckoutStart, PlanChange, PlanOut, PortalOut,
)
from app.services import billing as billing_svc
from app.services import modules as modules_svc
from app.services import plans
from app.services.billing_provider import BillingError, get_billing_provider

router = APIRouter(prefix="/billing", tags=["billing"])
log = logging.getLogger("invoiceiq.billing")


def _plan_out(p) -> PlanOut:
    return PlanOut(
        key=p.key, name=p.name, seats=p.seats, price_eur=p.price_eur,
        modules=sorted(p.modules), trial=p.trial,
    )


@router.get("", response_model=BillingOut)
async def get_billing(current: CurrentUser, db: DbSession):
    org = await db.get(Organization, current.org_id)
    plan = plans.plan_for(org.plan)
    return BillingOut(
        plan=_plan_out(plan),
        status=org.status,
        seats_used=await plans.active_seats(db, current.org_id),
        seats_limit=plan.seats,
        available_plans=[_plan_out(p) for p in plans.PLANS.values()],
        billing_enabled=settings.billing_enabled,
        has_subscription=bool(org.stripe_subscription_id),
    )


@router.put("/plan", response_model=BillingOut)
async def change_plan(body: PlanChange, current: CurrentUser, db: DbSession):
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can change the plan")
    if body.plan not in plans.PLANS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown plan")

    org = await db.get(Organization, current.org_id)
    target = plans.plan_for(body.plan)

    # When Stripe is live, a PAID plan change must go through Checkout/Portal so
    # entitlements never outrun payment; the webhook is the authority. The free
    # default plan can still be set directly (in-app cancel/downgrade).
    if settings.billing_enabled and target.price_eur:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Billing is connected — start a checkout session to change to a paid plan.",
        )

    # Downgrade guards: can't drop below current seat usage.
    used = await plans.active_seats(db, current.org_id)
    if used > target.seats:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{target.name} allows {target.seats} seats but {used} are in use. Remove members first.",
        )
    # ...or below required modules: disable add-ons the new plan doesn't include.
    enabled = await modules_svc.enabled_keys(db, current.org_id)
    for key in enabled:
        m = modules_svc.MODULES_BY_KEY.get(key)
        if m and not m.core and key not in target.modules:
            await modules_svc.set_enabled(db, current.org_id, key, False)

    org.plan = body.plan
    await db.commit()
    return await get_billing(current, db)


async def _ensure_customer(db, org: Organization, current) -> str:
    """Return the org's Stripe customer id, creating + persisting it on first use."""
    if org.stripe_customer_id:
        return org.stripe_customer_id
    provider = get_billing_provider()
    customer_id = await provider.ensure_customer(
        org_id=org.id, name=org.name, email=current.email
    )
    org.stripe_customer_id = customer_id
    await db.commit()
    return customer_id


@router.post("/checkout", response_model=CheckoutOut)
async def start_checkout(body: CheckoutStart, current: CurrentUser, db: DbSession):
    """Begin a Stripe Checkout session for a paid plan → returns a redirect URL."""
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can manage billing")
    if not settings.billing_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Billing is not configured")
    target = plans.PLANS.get(body.plan)
    if target is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown plan")
    if not target.price_eur:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That plan is not purchasable via checkout")

    org = await db.get(Organization, current.org_id)
    try:
        customer_id = await _ensure_customer(db, org, current)
        url = await get_billing_provider().create_checkout_url(
            customer_id=customer_id, plan_key=body.plan, org_id=org.id
        )
    except BillingError as exc:
        log.warning("checkout failed for org %s: %s", current.org_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return CheckoutOut(url=url)


@router.post("/portal", response_model=PortalOut)
async def open_portal(current: CurrentUser, db: DbSession):
    """Open the Stripe Customer Portal (manage payment method / cancel / invoices)."""
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can manage billing")
    if not settings.billing_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Billing is not configured")

    org = await db.get(Organization, current.org_id)
    if not org.stripe_customer_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No billing account yet — subscribe first")
    try:
        url = await get_billing_provider().create_portal_url(customer_id=org.stripe_customer_id)
    except BillingError as exc:
        log.warning("portal failed for org %s: %s", current.org_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return PortalOut(url=url)


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: DbSession):
    """Stripe → us. NO bearer auth: authenticity is the payload SIGNATURE.

    No `CurrentUser` runs, so the session carries no tenant context — the tenant
    is resolved from the Stripe customer id (unscoped lookup). Returns 200 for a
    *verified* event we chose to ignore (so Stripe stops retrying); only a
    signature/verification failure returns 400.
    """
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    provider = get_billing_provider()
    try:
        event = provider.parse_webhook(payload, signature)
    except BillingError as exc:
        log.warning("rejected Stripe webhook: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook")

    try:
        applied = await billing_svc.apply_subscription_event(db, event)
    except Exception:  # noqa: BLE001 - never 500 back to Stripe on a business fault
        log.exception("failed to apply Stripe event %s", event.event_id)
        await db.rollback()
        applied = False
    return {"received": True, "applied": applied}
