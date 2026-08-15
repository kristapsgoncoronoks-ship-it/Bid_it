"""Periodic enqueuer: turns "run daily" into idempotent queue entries.

The worker calls `enqueue_daily` once per loop-day. Each periodic job is keyed by
its date (`recurring.generate:2026-07-20`), so enqueuing it a hundred times a day
still yields exactly one job per org per day — the queue's idempotency does the
de-duplication. This keeps scheduling stateless: no cron row to drift, and any
worker can safely run it.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.organization import Organization
from app.services import billing, billing_usage, job_handlers, jobs, retention

# The jobs enqueued for every active tenant, once per day.
DAILY_KINDS = (
    job_handlers.RECURRING_GENERATE,
    job_handlers.DUNNING_RUN,
    job_handlers.AP_DUE_ALERTS,
    # The recycle bin's 30-day purge. In DAILY_KINDS (every tenant) rather than
    # alongside the retention purge below (only tenants with a configured
    # policy): 30 days is a promise made to every client the moment they delete
    # something, not an opt-in setting. A tenant with no retention policy would
    # otherwise keep a binned record invisible and immortal.
    job_handlers.BIN_PURGE,
)


async def enqueue_daily(db: AsyncSession, *, today: date | None = None) -> int:
    """Enqueue each daily periodic job for every organization (idempotent per day).

    Runs UNSCOPED (worker context) so it can see every tenant. Returns the number
    of NEW jobs actually enqueued."""
    today = today or date.today()
    org_ids = list(await db.scalars(select(Organization.id)))
    created = 0
    for org_id in org_ids:
        for kind in DAILY_KINDS:
            key = f"{kind}:{today.isoformat()}"
            before = await _live_exists(db, org_id, kind, key)
            await jobs.enqueue(db, kind, {}, org_id=org_id, idempotency_key=key)
            if not before:
                created += 1

    # Daily ECB refresh (WO-8): rates are GLOBAL reference data, so this is ONE
    # job per day TOTAL — never one per tenant (that would fetch the same feed N
    # times). The queue requires an org row (org_id NOT NULL + RLS), so the
    # lowest org id deterministically "carries" the global job; the handler only
    # writes the shared, org-less `ecb_rates` cache, so which tenant carries it
    # is irrelevant. Same idempotent `kind:date` key convention as DAILY_KINDS.
    if org_ids:
        carrier = min(org_ids)
        kind = job_handlers.FX_REFRESH
        key = f"{kind}:{today.isoformat()}"
        before = await _live_exists(db, carrier, kind, key)
        await jobs.enqueue(db, kind, {}, org_id=carrier, idempotency_key=key)
        if not before:
            created += 1

    # EveryPay recurring: enqueue an MIT charge only for tenants whose renewal is
    # due today (idempotent per org per due-day).
    for org_id in await billing.orgs_due_for_charge(db, today=today):
        kind = job_handlers.EVERYPAY_CHARGE
        key = f"{kind}:{today.isoformat()}"
        before = await _live_exists(db, org_id, kind, key)
        await jobs.enqueue(db, kind, {}, org_id=org_id, idempotency_key=key)
        if not before:
            created += 1

    # Dogfood subscription billing (H1.6): ONE job per day total, carried by the
    # designated platform org itself — it is a real `organizations` row, so it
    # satisfies the queue's org_id NOT NULL + RLS invariant without a synthetic
    # "carrier" (unlike the FX refresh, which has no natural owner). A no-op
    # unless `settings.dogfood_billing_enabled` (platform org configured AND no
    # live billing provider active); the handler itself is idempotent per
    # (subscriber, period), so a daily re-run costs nothing on days nothing is due.
    if settings.dogfood_billing_enabled and settings.platform_org_id in org_ids:
        kind = job_handlers.PLATFORM_BILLING_RUN
        key = f"{kind}:{today.isoformat()}"
        before = await _live_exists(db, settings.platform_org_id, kind, key)
        await jobs.enqueue(db, kind, {}, org_id=settings.platform_org_id, idempotency_key=key)
        if not before:
            created += 1

    # Retention purge: only for tenants that have configured a policy.
    for org_id in await retention.orgs_with_policy(db):
        kind = job_handlers.RETENTION_PURGE
        key = f"{kind}:{today.isoformat()}"
        before = await _live_exists(db, org_id, kind, key)
        await jobs.enqueue(db, kind, {}, org_id=org_id, idempotency_key=key)
        if not before:
            created += 1

    # Metered-usage reporting: only when Stripe is the active provider, for
    # subscribed tenants (idempotent per org per day; the handler reports deltas).
    if settings.active_billing_provider == "stripe":
        for org_id in await billing_usage.orgs_with_stripe(db):
            kind = job_handlers.USAGE_REPORT
            key = f"{kind}:{today.isoformat()}"
            before = await _live_exists(db, org_id, kind, key)
            await jobs.enqueue(db, kind, {}, org_id=org_id, idempotency_key=key)
            if not before:
                created += 1
    return created


async def _live_exists(db: AsyncSession, org_id: str, kind: str, key: str) -> bool:
    from app.models.job import Job

    found = await db.scalar(
        select(Job.id).where(Job.org_id == org_id, Job.kind == kind, Job.idempotency_key == key)
    )
    return found is not None
