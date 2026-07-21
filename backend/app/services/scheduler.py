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
DAILY_KINDS = (job_handlers.RECURRING_GENERATE, job_handlers.DUNNING_RUN)


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

    # EveryPay recurring: enqueue an MIT charge only for tenants whose renewal is
    # due today (idempotent per org per due-day).
    for org_id in await billing.orgs_due_for_charge(db, today=today):
        kind = job_handlers.EVERYPAY_CHARGE
        key = f"{kind}:{today.isoformat()}"
        before = await _live_exists(db, org_id, kind, key)
        await jobs.enqueue(db, kind, {}, org_id=org_id, idempotency_key=key)
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
