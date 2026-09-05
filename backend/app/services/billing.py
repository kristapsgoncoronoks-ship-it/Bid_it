"""Billing orchestration: apply a verified provider result to a tenant's
entitlements, idempotently.

The PROVIDER is the AUTHORITY (ADR-0013): a Stripe subscription webhook, or an
EveryPay payment we verify server-side, is what moves a tenant between plans /
lifecycle states — never the browser redirect alone. This module holds the
*pure-ish* application logic (resolve tenant → set plan/status → reconcile add-on
modules) so it is unit-testable with a fabricated event and no SDK. Idempotency
(ADR-0011) is enforced via the `processed_stripe_events` ledger (used for both
providers — a generic billing-event ledger despite the historical name).
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import reset_current_org, set_current_org
from app.models.billing_event import ProcessedStripeEvent
from app.models.billing_payment import BillingPayment
from app.models.organization import Organization
from app.services import archive, plans
from app.services import modules as modules_svc
from app.services.billing_provider import SubscriptionEvent, get_billing_provider
from app.services.recurring import _add_months

log = logging.getLogger("invoiceiq.billing")


async def record_event_once(db: AsyncSession, event_id: str, event_type: str) -> bool:
    """Return True the FIRST time an event id is seen; False on a redelivery.

    Relies on the unique index for correctness under concurrent redelivery: two
    workers racing the same event → one commits, the other hits IntegrityError
    and is treated as a duplicate.
    """
    if not event_id:
        return True  # unsigned/fabricated path (tests) — always apply
    db.add(ProcessedStripeEvent(event_id=event_id, event_type=event_type))
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return False
    return True


async def apply_subscription_event(db: AsyncSession, event: SubscriptionEvent) -> bool:
    """Apply a verified subscription event to the tenant it belongs to.

    Idempotent + safe to call with an unmatched customer (no tenant → no-op).
    Returns True when a tenant was updated, False when the event was a duplicate,
    unmatched, or carried nothing actionable. Never raises on a business no-op.
    """
    if not await record_event_once(db, event.event_id, event.event_type):
        log.info("stripe event %s already processed; skipping", event.event_id)
        return False

    if not event.customer_id or (event.plan_key is None and event.status is None):
        # Nothing to apply (unrelated event type, or no customer on the object).
        await db.commit()
        return False

    org = await db.scalar(
        select(Organization).where(Organization.stripe_customer_id == event.customer_id)
    )
    if org is None:
        log.warning(
            "stripe event %s references unknown customer %s", event.event_id, event.customer_id
        )
        await db.commit()
        return False

    # Bind the tenant guard to the resolved org for the entitlement mutation —
    # the webhook has no authenticated context, and module writes touch a
    # tenant-scoped table.
    token = set_current_org(org.id)
    try:
        changed = await _apply_to_org(db, org, event)
        await db.commit()
    finally:
        reset_current_org(token)
    return changed


async def _apply_to_org(db: AsyncSession, org: Organization, event: SubscriptionEvent) -> bool:
    """Set plan/status on a resolved org and reconcile add-on modules.

    A canceled subscription drops the tenant to the free default plan (they keep
    core modules, lose paid add-ons) rather than deleting data. Downgrades never
    remove seats automatically — that would eject users mid-cycle; the seat cap
    simply blocks *new* seats until they fit (same rule as `PUT /billing/plan`).
    """
    changed = False

    if event.subscription_id and org.stripe_subscription_id != event.subscription_id:
        org.stripe_subscription_id = event.subscription_id
        changed = True

    if event.status and event.status != org.status:
        org.status = event.status
        changed = True

    # A cancellation returns the tenant to the free plan; otherwise honor the
    # plan the (active) subscription maps to.
    target_plan = None
    if event.status == "canceled":
        target_plan = plans.DEFAULT_PLAN
    elif event.plan_key and event.plan_key in plans.PLANS:
        target_plan = event.plan_key

    if target_plan and target_plan != org.plan:
        org.plan = target_plan
        changed = True
        # WO-AD: retention rides the ladder, so a plan change is the moment a
        # longer promise starts — and it has to reach rows already archived
        # (extend-only). Flush first: `retention_years` reads via a SELECT.
        await db.flush()
        await archive.restamp_to_effective(db, org.id)

    if changed:
        await _reconcile_modules(db, org)
    return changed


async def _reconcile_modules(db: AsyncSession, org: Organization) -> None:
    """Disable any non-core add-on the tenant's current plan no longer includes.

    Shared with `PUT /billing/plan`. Runs scoped to the resolved org so the
    tenant guard applies even in the (unscoped) webhook context — the caller sets
    `set_current_org` before invoking us.
    """
    target = plans.plan_for(org.plan)
    enabled = await modules_svc.enabled_keys(db, org.id)
    for key in enabled:
        m = modules_svc.MODULES_BY_KEY.get(key)
        if m and not m.core and key not in target.modules:
            await modules_svc.set_enabled(db, org.id, key, False)


# --- EveryPay redirect confirmation (server-side verify) -------------------


async def confirm_redirect_payment(db: AsyncSession, reference: str) -> bool:
    """Verify a redirect-flow payment with the provider and apply the plan.

    Called from BOTH the browser return AND the server callback (either can win),
    so it is idempotent: the `BillingPayment.state` short-circuit + the event
    ledger ensure the plan is applied exactly once. Returns True on a settled
    payment. Never trusts the redirect — the status comes from a server-side call
    to the provider.
    """
    pay = await db.scalar(select(BillingPayment).where(BillingPayment.reference == reference))
    if pay is None:
        log.warning("everypay confirm: unknown payment reference %s", reference)
        return False
    if pay.state == "settled":
        return True  # already applied by the other path

    provider = get_billing_provider()
    status = await provider.verify_payment(reference=reference, order_reference=pay.order_reference)
    if status.state == "pending":
        return False  # not final yet; the callback (or a later return) will settle it

    org = await db.get(Organization, pay.org_id)
    if org is None:  # tenant deleted mid-flight
        pay.state = "failed"
        await db.commit()
        return False

    tok = set_current_org(org.id)
    try:
        if status.state == "settled":
            if await record_event_once(db, f"everypay:{reference}", "everypay.settled"):
                org.plan = pay.plan_key
                org.status = "active"
                if status.token:
                    org.everypay_token = status.token
                org.everypay_next_charge = _add_months(date.today(), 1)
                await _reconcile_modules(db, org)
                # WO-AD: same re-stamp as the Stripe path — one implementation.
                await db.flush()
                await archive.restamp_to_effective(db, org.id)
            pay.state = "settled"
            await db.commit()
            return True
        pay.state = "failed"  # failed | voided | abandoned
        await db.commit()
        return False
    finally:
        reset_current_org(tok)


# --- EveryPay recurring (merchant-initiated charges) -----------------------


async def orgs_due_for_charge(db: AsyncSession, *, today: date) -> list[str]:
    """Ids of EveryPay-subscribed tenants whose next paid charge is due today.

    Runs UNSCOPED (worker/scheduler context) so it sees every tenant. Non-paid
    plans are filtered out — only a plan with a price is charged."""
    orgs = list(
        await db.scalars(
            select(Organization).where(
                Organization.everypay_token.is_not(None),
                Organization.everypay_next_charge.is_not(None),
                Organization.everypay_next_charge <= today,
            )
        )
    )
    return [o.id for o in orgs if plans.plan_for(o.plan).price_eur]


async def charge_renewal(db: AsyncSession, org_id: str, *, today: date | None = None) -> dict:
    """Charge one tenant's recurring EveryPay MIT for the current period.

    Idempotent per (org, due-day) via the event ledger. On success the next
    charge date advances one month; on a declined charge the tenant is suspended
    (a following day's scheduler re-enqueues → simple retry/dunning). Never raises
    on a business outcome."""
    today = today or date.today()
    org = await db.get(Organization, org_id)
    if org is None or not org.everypay_token:
        return {"charged": False, "reason": "no-token"}
    plan = plans.plan_for(org.plan)
    if not plan.price_eur:
        return {"charged": False, "reason": "not-paid-plan"}

    tok = set_current_org(org.id)
    try:
        if not await record_event_once(
            db, f"everypay:mit:{org_id}:{today.isoformat()}", "everypay.mit"
        ):
            return {"charged": False, "reason": "duplicate"}
        # BE-005 (audit 2026-09-05): the dedupe row is COMMITTED before the
        # provider is called. It used to be flushed only, with the commit after
        # the charge — so a timeout or worker death after EveryPay had accepted
        # the charge rolled the claim back, the job retried with backoff, and
        # the card was charged again for the same period. Losing the race in
        # the other direction (claim committed, charge never happened) costs one
        # missed charge that the next period's key picks up; that is the
        # recoverable side.
        await db.commit()
        provider = get_billing_provider()
        status = await provider.charge_mit(
            token=org.everypay_token,
            amount_eur=Decimal(plan.price_eur),
            order_reference=f"{org_id[:8]}-{org.plan}-{today.isoformat()}",
        )
        if status.state == "settled":
            org.everypay_next_charge = _add_months(org.everypay_next_charge or today, 1)
            if org.status != "active":
                org.status = "active"
            result: dict[str, object] = {"charged": True, "amount_eur": plan.price_eur}
        else:
            org.status = "suspended"
            result = {"charged": False, "reason": status.state}
        await db.commit()
        return result
    finally:
        reset_current_org(tok)
