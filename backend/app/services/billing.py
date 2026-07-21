"""Billing orchestration: apply a verified subscription event to a tenant's
entitlements, idempotently.

The webhook is the AUTHORITY (ADR-0013): a Stripe subscription change is what
moves a tenant between plans / lifecycle states — not the browser redirect. This
module holds the *pure-ish* application logic (resolve tenant → set plan/status →
reconcile add-on modules) so it is unit-testable with a fabricated event and no
Stripe SDK. Idempotency (ADR-0011) is enforced via `processed_stripe_events`.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import reset_current_org, set_current_org
from app.models.billing_event import ProcessedStripeEvent
from app.models.organization import Organization
from app.services import modules as modules_svc
from app.services import plans
from app.services.billing_provider import SubscriptionEvent

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
        log.warning("stripe event %s references unknown customer %s", event.event_id, event.customer_id)
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
