from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentOrg, CurrentUser, DbSession, require_perm
from app.core import authz
from app.core.config import settings
from app.models.billing_payment import BillingPayment
from app.models.organization import Organization
from app.schemas.tenancy import (
    BillingOut,
    CheckoutOut,
    CheckoutStart,
    PlanChange,
    PlanOut,
    PortalOut,
)
from app.services import billing as billing_svc
from app.services import modules as modules_svc
from app.services import plans
from app.services.billing_provider import BillingError, get_billing_provider

# Structural authorization (ADR-0024): declared PER-ROUTE because the Stripe/
# EveryPay webhook + redirect endpoints authenticate by signature/reference (see
# PUBLIC_ROUTES). Everything a user calls here manages the subscription —
# BILLING_MANAGE (owner-only), including the read (it exposes subscription
# state; previously any member could read it — tightened by ADR-0024).
router = APIRouter(prefix="/billing", tags=["billing"])
_MANAGE = [Depends(require_perm(authz.Permission.BILLING_MANAGE))]
log = logging.getLogger("invoiceiq.billing")


def _plan_out(p) -> PlanOut:
    return PlanOut(
        key=p.key,
        name=p.name,
        seats=p.seats,
        price_eur=p.price_eur,
        modules=sorted(p.modules),
        trial=p.trial,
    )


@router.get("", response_model=BillingOut, dependencies=_MANAGE)
async def get_billing(current: CurrentUser, db: DbSession, org: CurrentOrg):
    plan = plans.plan_for(org.plan)
    return BillingOut(
        plan=_plan_out(plan),
        status=org.status,
        seats_used=await plans.active_seats(db, current.org_id),
        seats_limit=plan.seats,
        available_plans=[_plan_out(p) for p in plans.PLANS.values()],
        billing_enabled=settings.billing_enabled,
        billing_provider=settings.active_billing_provider,
        has_subscription=bool(org.stripe_subscription_id or org.everypay_token),
    )


@router.put("/plan", response_model=BillingOut, dependencies=_MANAGE)
async def change_plan(body: PlanChange, current: CurrentUser, db: DbSession, org: CurrentOrg):
    if body.plan not in plans.PLANS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown plan")

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
    return await get_billing(current, db, org)


async def _ensure_customer(db, org: Organization, current) -> str:
    """Return the org's Stripe customer id, creating + persisting it on first use."""
    if org.stripe_customer_id:
        return org.stripe_customer_id
    provider = get_billing_provider()
    customer_id = await provider.ensure_customer(org_id=org.id, name=org.name, email=current.email)
    org.stripe_customer_id = customer_id
    await db.commit()
    return customer_id


@router.post("/checkout", response_model=CheckoutOut, dependencies=_MANAGE)
async def start_checkout(body: CheckoutStart, current: CurrentUser, db: DbSession, org: CurrentOrg):
    """Start a hosted payment for a paid plan → returns a redirect URL.

    Provider-agnostic: Stripe starts a subscription Checkout; EveryPay starts a
    hosted card payment and we persist a `BillingPayment` so the return/callback
    can verify it server-side and apply the plan.
    """
    if not settings.billing_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Billing is not configured")
    target = plans.PLANS.get(body.plan)
    if target is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown plan")
    if not target.price_eur:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "That plan is not purchasable via checkout"
        )

    provider = get_billing_provider()
    order_reference = f"iiq-{org.id[:8]}-{body.plan}-{uuid.uuid4().hex[:10]}"
    try:
        # Subscription providers (Stripe) need a customer; redirect ones don't.
        customer_id = (
            await _ensure_customer(db, org, current) if provider.kind == "subscription" else None
        )
        session = await provider.start_checkout(
            org_id=org.id,
            plan_key=body.plan,
            amount_eur=float(target.price_eur),
            order_reference=order_reference,
            customer_id=customer_id,
        )
    except BillingError as exc:
        log.warning("checkout failed for org %s: %s", current.org_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    if provider.kind == "redirect" and session.reference:
        db.add(
            BillingPayment(
                org_id=org.id,
                provider=provider.name,
                reference=session.reference,
                order_reference=order_reference,
                plan_key=body.plan,
                amount_eur=float(target.price_eur),
                state="initial",
            )
        )
        await db.commit()
    return CheckoutOut(url=session.url)


@router.post("/portal", response_model=PortalOut, dependencies=_MANAGE)
async def open_portal(current: CurrentUser, db: DbSession, org: CurrentOrg):
    """Open the Stripe Customer Portal (manage payment method / cancel / invoices).

    Subscription providers only — EveryPay has no hosted portal."""
    provider = get_billing_provider()
    if provider.kind != "subscription":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This payment method has no customer portal"
        )

    if not org.stripe_customer_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No billing account yet — subscribe first")
    try:
        url = await provider.create_portal_url(customer_id=org.stripe_customer_id)
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


async def _confirm_everypay(db, reference: str | None) -> bool:
    """Verify + apply an EveryPay payment by reference (idempotent, never raises)."""
    if not reference:
        return False
    try:
        return await billing_svc.confirm_redirect_payment(db, reference)
    except Exception:  # noqa: BLE001
        log.exception("failed to confirm EveryPay payment %s", reference)
        await db.rollback()
        return False


@router.get("/everypay/return", include_in_schema=False)
async def everypay_return(request: Request, db: DbSession):
    """Browser redirect back from EveryPay's hosted page. We VERIFY server-side
    (never trust the redirect), apply the plan, then bounce to the SPA."""
    ok = await _confirm_everypay(db, request.query_params.get("payment_reference"))
    dest = settings.billing_success_url if ok else settings.billing_cancel_url
    return RedirectResponse(dest, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/everypay/callback", include_in_schema=False)
async def everypay_callback(request: Request, db: DbSession):
    """EveryPay server-to-server notification. Same verify+apply as the return,
    so whichever arrives first settles the payment (the other is a no-op)."""
    reference = request.query_params.get("payment_reference")
    if not reference:
        try:
            form = await request.form()
            value = form.get("payment_reference")
            reference = value if isinstance(value, str) else None
        except Exception:  # noqa: BLE001 - no/blank form body
            reference = None
    applied = await _confirm_everypay(db, reference)
    return {"received": True, "applied": applied}
