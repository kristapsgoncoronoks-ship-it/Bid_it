from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import (
    DbSession,
    SuspendedTolerantOrg,
    SuspendedTolerantUser,
    require_perm_suspended_tolerant,
)
from app.core import authz
from app.core.config import settings
from app.core.tenant import reset_current_org, set_current_org
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
from app.services import archive, job_handlers, jobs, plans
from app.services import billing as billing_svc
from app.services import modules as modules_svc
from app.services.billing_provider import (
    CHECKOUT_TTL_SECONDS,
    BillingError,
    active_provider_kind,
    get_billing_provider,
)

# Structural authorization (ADR-0024): declared PER-ROUTE because the Stripe/
# EveryPay webhook + redirect endpoints authenticate by signature/reference (see
# PUBLIC_ROUTES). Everything a user calls here manages the subscription —
# BILLING_MANAGE (owner-only), including the read (it exposes subscription
# state; previously any member could read it — tightened by ADR-0024).
router = APIRouter(prefix="/billing", tags=["billing"])
# PROD-001 (audit 2026-09-05): the billing surface is reachable for a SUSPENDED
# organization's billing manager. A declined card sets the org suspended, and
# the active-only gate then locked the one person who could fix it out of the
# screen that takes the card. Same permission, same 403 for everyone else; a
# canceled org is still a 401 here.
_MANAGE = [Depends(require_perm_suspended_tolerant(authz.Permission.BILLING_MANAGE))]
log = logging.getLogger("invoiceiq.billing")


def _aware(ts: datetime) -> datetime:
    """SQLite hands back naive UTC timestamps; Postgres aware ones."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _holds_live_subscription(provider_kind: str, org: Organization) -> bool:
    """True when the ACTIVE provider (by kind — never constructed here, the
    Stripe SDK is optional) holds a subscription object for this workspace
    that a new hosted payment would duplicate (BE-022). Stripe: a
    non-canceled `stripe_subscription_id` (the webhook clears it on cancel;
    `org.status` mirrors the subscription status). EveryPay has no
    subscription object — its stored token IS the recurring charge, so a new
    hosted payment is the right path and the Checkout guard never fires for
    it; the token still counts as "has a subscription" for the page."""
    if provider_kind == "subscription":
        return bool(org.stripe_subscription_id) and org.status != "canceled"
    if provider_kind == "redirect":
        return bool(org.everypay_token)
    return False


_SUBSCRIBER_409 = (
    "This workspace already has a subscription. Change the plan, the payment method "
    "or cancel through Manage billing instead of starting a new checkout."
)


def _plan_out(p) -> PlanOut:
    return PlanOut(
        key=p.key,
        name=p.name,
        seats=p.seats,
        price_eur=p.price_eur,
        modules=sorted(p.modules),
        trial=p.trial,
        purchasable=settings.plan_purchasable(p.key, p.price_eur),
        archive_retention_years=p.archive_retention_years,
    )


@router.get("", response_model=BillingOut, dependencies=_MANAGE)
async def get_billing(current: SuspendedTolerantUser, db: DbSession, org: SuspendedTolerantOrg):
    plan = plans.plan_for(org.plan)
    return BillingOut(
        plan=_plan_out(plan),
        status=org.status,
        seats_used=await plans.active_seats(db, current.org_id),
        seats_limit=plan.seats,
        available_plans=[_plan_out(p) for p in plans.PLANS.values()],
        billing_enabled=settings.billing_enabled,
        billing_provider=settings.active_billing_provider,
        # The ACTIVE provider's object only (R6 review A4): a Stripe deployment
        # whose org still carries an EveryPay token from a provider switch must
        # be offered Checkout, not a Portal it has no account at.
        has_subscription=_holds_live_subscription(active_provider_kind(), org),
    )


@router.put("/plan", response_model=BillingOut, dependencies=_MANAGE)
async def change_plan(
    body: PlanChange, current: SuspendedTolerantUser, db: DbSession, org: SuspendedTolerantOrg
):
    if body.plan not in plans.PLANS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown plan")

    target = plans.plan_for(body.plan)

    # A plan with no listed price (`price_eur is None`, e.g. Enterprise) is
    # "contact us" custom pricing, never self-service — unconditionally, whether
    # or not a billing provider is wired. This guard must NOT be folded into the
    # billing_enabled check below: `None` is falsy in Python, so
    # `billing_enabled and target.price_eur` is False for a None-priced plan
    # regardless of billing_enabled, which previously let any org owner
    # self-upgrade to Enterprise for free even in a live-Stripe deployment (R5b).
    if target.price_eur is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{target.name} is not self-service — contact sales to switch to this plan.",
        )

    # When Stripe is live, a PAID plan change must go through Checkout/Portal so
    # entitlements never outrun payment; the webhook is the authority. The free
    # default plan can still be set directly (in-app cancel/downgrade) — by a
    # workspace WITHOUT a live Stripe subscription. BE-023 (R6): a subscriber
    # setting the free plan here dropped their entitlements at once while the
    # provider kept charging, and the next renewal webhook put the paid plan
    # back; the only honest cancel is the Portal's, whose `canceled` webhook
    # applies the free plan for real (`_apply_to_org`). A business-behaviour
    # change, recorded with the owner's residual choices in DECISIONS §25.
    # EveryPay is not gated here: its recurring charge is ours to stop
    # (`billing.everypay_recurring`), and it stops with the plan.
    if settings.billing_enabled and target.price_eur:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Billing is connected — start a checkout session to change to a paid plan.",
        )
    kind = active_provider_kind()
    if kind == "subscription" and _holds_live_subscription(kind, org):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This workspace has a subscription — cancel it through Manage billing; "
            "the free plan applies when the provider confirms the cancellation.",
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
    # WO-AD: retention rides the ladder; an in-app switch to a longer-retention
    # plan must reach rows already archived, extend-only.
    await db.flush()
    await archive.restamp_to_effective(db, org.id)
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
async def start_checkout(
    body: CheckoutStart, current: SuspendedTolerantUser, db: DbSession, org: SuspendedTolerantOrg
):
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
    # WO-AD: a priced plan the active provider has no price for is a
    # configuration gap, not a gateway fault — say so as a 400 the SPA can
    # render, instead of letting the provider raise and surfacing a 502.
    if not settings.plan_purchasable(target.key, target.price_eur):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{target.name} is not yet available for purchase — no price is configured for it.",
        )

    provider = get_billing_provider()
    # BE-022 (audit 2026-09-05, found by the R4 review): a Checkout for a
    # customer who already holds a subscription creates a SECOND subscription
    # at the provider — two charges a month, one plan. Until R4 the SPA's
    # disabled button was the only guard and it expired silently at 90 s. A
    # subscription provider changes plans and payment methods through its
    # Portal (`POST /billing/portal`); a redirect provider (EveryPay) has no
    # subscription object, so its repeat payments are the recurring charge and
    # a new hosted payment is the right path. The webhook clears the id on
    # cancel (a canceled subscription cannot be resumed), so a reactivated
    # workspace is not locked out by a stale one.
    if provider.kind == "subscription" and _holds_live_subscription(provider.kind, org):
        raise HTTPException(status.HTTP_409_CONFLICT, _SUBSCRIBER_409)
    # The window BEFORE the webhook lands (R6 review A1): the subscription id
    # arrives with `checkout.session.completed`, applied by the worker, so a
    # second Checkout started meanwhile — a second tab, a second billing
    # manager, the page's 90 s wait running out — would open a second
    # subscription. Every started Checkout is persisted as a `BillingPayment`
    # in state `initial`; another one is refused while an unexpired one exists.
    # The provider closes it (`completed` → settled, `expired` → abandoned) or
    # it ages out with the session's own expiry (CHECKOUT_TTL_SECONDS).
    if provider.kind == "subscription":
        open_since = datetime.now(UTC) - timedelta(seconds=CHECKOUT_TTL_SECONDS)
        in_flight = await db.scalar(
            select(BillingPayment.created_at).where(
                BillingPayment.org_id == org.id,
                BillingPayment.provider == provider.name,
                BillingPayment.state == "initial",
                BillingPayment.created_at >= open_since,
            )
        )
        if in_flight is not None:
            minutes = int((datetime.now(UTC) - _aware(in_flight)).total_seconds() // 60)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"A checkout for this workspace is still open (started {minutes} min ago). "
                "Finish it in the tab where it was started, or wait for it to expire "
                f"({CHECKOUT_TTL_SECONDS // 60} min) before starting another.",
            )
    order_reference = f"iiq-{org.id[:8]}-{body.plan}-{uuid.uuid4().hex[:10]}"
    try:
        # Subscription providers (Stripe) need a customer; redirect ones don't.
        customer_id = (
            await _ensure_customer(db, org, current) if provider.kind == "subscription" else None
        )
        session = await provider.start_checkout(
            org_id=org.id,
            plan_key=body.plan,
            amount_eur=Decimal(target.price_eur),
            order_reference=order_reference,
            customer_id=customer_id,
        )
    except BillingError as exc:
        log.warning("checkout failed for org %s: %s", current.org_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    if session.reference:
        # Redirect providers: the row the return/callback verifies against.
        # Subscription providers: the in-flight marker above (state `initial`
        # until the provider's webhook settles or abandons it).
        db.add(
            BillingPayment(
                org_id=org.id,
                provider=provider.name,
                reference=session.reference,
                order_reference=order_reference,
                plan_key=body.plan,
                amount_eur=Decimal(target.price_eur),
                state="initial",
            )
        )
        await db.commit()
    return CheckoutOut(url=session.url)


@router.post("/portal", response_model=PortalOut, dependencies=_MANAGE)
async def open_portal(current: SuspendedTolerantUser, db: DbSession, org: SuspendedTolerantOrg):
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
    is resolved from the Stripe customer id (unscoped lookup).

    BILL-REL-001 (Lago reference integration 2026-09-07): an actionable, matched
    event is acknowledged only after it is COMMITTED as a durable
    `billing.apply_subscription_event` job keyed on the Stripe event id. The
    route used to apply the event inline and answer 200 even when applying
    failed (`applied: false`), which told Stripe "delivered" about an event we
    had lost. Now: a verified event we chose to ignore is a harmless 200
    (`queued: false`); a matched event is 200 once its job row is committed
    (`queued: true`, `created` false on a redelivery — the queue's idempotency
    key dedupes); a failure to persist the job is 503 so Stripe retries; only a
    signature/verification failure is 400. The body's `applied` field is gone:
    application happens on the worker, under the queue's retry/dead-letter rules.
    """
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    provider = get_billing_provider()
    try:
        event = provider.parse_webhook(payload, signature)
    except BillingError as exc:
        log.warning("rejected Stripe webhook: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook")

    org_id = await billing_svc.subscription_event_org_id(db, event)
    if org_id is None:
        if event.customer_id and (event.plan_key is not None or event.status is not None):
            log.warning(
                "verified Stripe event %s references unknown customer %s",
                event.event_id,
                event.customer_id,
            )
        return {"received": True, "queued": False, "created": False}

    job_payload = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "customer_id": event.customer_id,
        "subscription_id": event.subscription_id,
        "plan_key": event.plan_key,
        "status": event.status,
        "checkout_session_id": event.checkout_session_id,
    }
    # The job row is tenant-scoped; bind the guard to the resolved org for the
    # insert exactly as `apply_subscription_event` does for the entitlement write.
    token = set_current_org(org_id)
    try:
        _job, created = await jobs.enqueue_with_outcome(
            db,
            job_handlers.STRIPE_SUBSCRIPTION_EVENT,
            job_payload,
            org_id=org_id,
            idempotency_key=event.event_id or None,
        )
    except Exception as exc:  # noqa: BLE001 - no durable ownership → ask Stripe to retry
        await db.rollback()
        log.exception("failed to durably enqueue Stripe event %s", event.event_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Billing event processing unavailable"
        ) from exc
    finally:
        reset_current_org(token)
    return {"received": True, "queued": True, "created": created}


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
