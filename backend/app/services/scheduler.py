"""Periodic enqueuer: turns "run daily" into idempotent queue entries.

The worker calls `enqueue_daily` once per loop-day. Each periodic job is keyed by
its date (`recurring.generate:2026-07-20`), so enqueuing it a hundred times a day
still yields exactly one job per org per day — the queue's idempotency does the
de-duplication. This keeps scheduling stateless: no cron row to drift, and any
worker can safely run it.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.job import Job
from app.models.organization import Organization
from app.services import billing, billing_usage, job_handlers, jobs, retention

#: ARCH-013 / PERF-011: one worker runs the daily sweep at a time. A Postgres
#: transaction-scoped advisory lock keyed on this name; the other workers see
#: it held and skip — the idempotency keys already made a second run harmless,
#: this makes it free. SQLite (one process) has no lock and needs none.
ADVISORY_LOCK_KEY = "scheduler.enqueue_daily"

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
    # The archive's expiry purge — the END of the chain the bin purge feeds.
    # Every tenant, for the same reason as BIN_PURGE: "kept for N years, then
    # removed" is stamped on every archived record, not an opt-in setting, and
    # until this ran nothing enforced the "then removed" half.
    job_handlers.ARCHIVE_PURGE,
    # The pre-expiry notice for the archive — every tenant, because "nothing
    # leaves the archive without the owner having been told first" is a promise
    # on the store itself, not a setting anyone opted into.
    job_handlers.ARCHIVE_NOTICE,
    # PROD-009: destroy the bytes of every export whose one-time link has died.
    # Every tenant, for the same reason as BIN_PURGE — "this link works once and
    # expires in seven days" is a promise about the FILE, and until this ran the
    # file outlived every link that ever pointed at it. A tenant that has never
    # asked for an export no-ops in two queries.
    job_handlers.EXPORT_PURGE,
    # WO-J: the automation sweep — every tenant daily; tenants without
    # published rules no-op in one query.
    "automation.sweep",
)


async def enqueue_daily(db: AsyncSession, *, today: date | None = None) -> int:
    """Enqueue each daily periodic job for every organization (idempotent per day).

    Runs UNSCOPED (worker context) so it can see every tenant. Returns the number
    of NEW jobs actually enqueued."""
    today = today or date.today()
    if not await _take_daily_lock(db):
        return 0
    org_ids = list(await db.scalars(select(Organization.id)))
    day = today.isoformat()

    # ARCH-013 / PERF-011 (audit 2026-09-05): this used to cost every worker,
    # every tick of a new day, tenants × kinds × (an existence SELECT, the
    # enqueue's own pre-check, an INSERT and a COMMIT) — and every worker paid
    # it in full even when another had already scheduled the day. Now the
    # wanted (org, kind) pairs are built first, the ones already on the queue
    # for today are read in ONE statement, only the missing ones are inserted,
    # and the batch commits once. The idempotency keys are unchanged, so a
    # concurrent sweep that slips past the advisory lock still cannot
    # duplicate a job.
    wanted: list[tuple[str, str]] = [(org_id, kind) for org_id in org_ids for kind in DAILY_KINDS]

    # Daily ECB refresh (WO-8): rates are GLOBAL reference data, so this is ONE
    # job per day TOTAL — never one per tenant (that would fetch the same feed N
    # times). The queue requires an org row (org_id NOT NULL + RLS), so the
    # lowest org id deterministically "carries" the global job; the handler only
    # writes the shared, org-less `ecb_rates` cache, so which tenant carries it
    # is irrelevant. Same idempotent `kind:date` key convention as DAILY_KINDS.
    if org_ids:
        wanted.append((min(org_ids), job_handlers.FX_REFRESH))

    # EveryPay recurring: enqueue an MIT charge only for tenants whose renewal is
    # due today (idempotent per org per due-day).
    for org_id in await billing.orgs_due_for_charge(db, today=today):
        wanted.append((org_id, job_handlers.EVERYPAY_CHARGE))

    # Dogfood subscription billing (H1.6): ONE job per day total, carried by the
    # designated platform org itself — it is a real `organizations` row, so it
    # satisfies the queue's org_id NOT NULL + RLS invariant without a synthetic
    # "carrier" (unlike the FX refresh, which has no natural owner). A no-op
    # unless `settings.dogfood_billing_enabled` (platform org configured AND no
    # live billing provider active); the handler itself is idempotent per
    # (subscriber, period), so a daily re-run costs nothing on days nothing is due.
    if settings.dogfood_billing_enabled and settings.platform_org_id in org_ids:
        wanted.append((settings.platform_org_id, job_handlers.PLATFORM_BILLING_RUN))

    # Retention purge: only for tenants that have configured a policy.
    for org_id in await retention.orgs_with_policy(db):
        wanted.append((org_id, job_handlers.RETENTION_PURGE))

    # Metered-usage reporting: only when Stripe is the active provider, for
    # subscribed tenants (idempotent per org per day; the handler reports deltas).
    if settings.active_billing_provider == "stripe":
        for org_id in await billing_usage.orgs_with_stripe(db):
            wanted.append((org_id, job_handlers.USAGE_REPORT))

    already = await _already_queued_today(db, wanted, day)
    created = 0
    for org_id, kind in wanted:
        if (org_id, kind) in already:
            continue
        await jobs.enqueue(
            db, kind, {}, org_id=org_id, idempotency_key=f"{kind}:{day}", commit=False
        )
        created += 1
    await db.commit()
    return created


async def _take_daily_lock(db: AsyncSession) -> bool:
    """Postgres: a transaction-scoped advisory lock so only one worker sweeps;
    False means another holds it. SQLite: always True (single process)."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return True
    return bool(
        await db.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": ADVISORY_LOCK_KEY}
        )
    )


async def _already_queued_today(
    db: AsyncSession, wanted: list[tuple[str, str]], day: str
) -> set[tuple[str, str]]:
    """The (org, kind) pairs that already have today's job — any status: a
    finished one is the same unit of work (BE-004), and the enqueue would only
    hand it back. One statement, keyed on the day's idempotency keys."""
    if not wanted:
        return set()
    keys = {f"{kind}:{day}" for _org, kind in wanted}
    rows = await db.execute(
        select(Job.org_id, Job.kind).where(
            Job.org_id.in_({org for org, _kind in wanted}), Job.idempotency_key.in_(keys)
        )
    )
    return {(org, kind) for org, kind in rows}
