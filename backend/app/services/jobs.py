"""A durable, DB-backed job queue with at-most-one-worker claim + retries.

Rows in the `jobs` table ARE the queue. This module is the whole contract:

  enqueue()      append a job (idempotent when an idempotency_key is given)
  claim()        atomically lease the oldest ready job to one worker
  run_once()     claim → dispatch to the registered handler → complete/fail
  reclaim_stale() return jobs whose worker died (stale lease) to the queue

Design goals: portable (SQLite + Postgres), idempotent (a re-enqueue with the
same key is a no-op; a handler that runs twice must be safe), and never-lost (a
crashed worker's job is reclaimed, a failing job retries with exponential
backoff and finally lands in the `dead` dead-letter state).

Handlers register with @handler("kind") and receive (db, payload, job). The job
runs INSIDE its tenant's scope (org context is set around dispatch) so handler
queries and audit attribution are tenant-correct.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import reset_current_org, set_current_org
from app.models import job as jobmodel
from app.models.job import Job

log = logging.getLogger("invoiceiq.jobs")

# Retry backoff: base_seconds * 2**(attempt-1), capped. attempt is 1-based.
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600
# A job leased longer than this is presumed abandoned (worker crashed).
STALE_LEASE_SECONDS = 300

Handler = Callable[[AsyncSession, dict, Job], Awaitable[dict | None]]
_HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Register the coroutine that processes jobs of `kind`."""

    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


def registered_kinds() -> tuple[str, ...]:
    return tuple(sorted(_HANDLERS))


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(UTC)


def _backoff(attempts: int) -> int:
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict | None = None,
    *,
    org_id: str,
    idempotency_key: str | None = None,
    max_attempts: int = 5,
    run_after: datetime | None = None,
    commit: bool = True,
) -> Job:
    """Append a job. With an `idempotency_key`, a matching LIVE job (queued or
    running) is returned instead of inserting a duplicate."""
    if idempotency_key is not None:
        existing = await db.scalar(
            select(Job).where(
                Job.org_id == org_id,
                Job.kind == kind,
                Job.idempotency_key == idempotency_key,
                Job.status.in_((jobmodel.QUEUED, jobmodel.RUNNING)),
            )
        )
        if existing is not None:
            return existing

    job = Job(
        org_id=org_id,
        kind=kind,
        payload_json=json.dumps(payload or {}),
        idempotency_key=idempotency_key,
        status=jobmodel.QUEUED,
        attempts=0,
        max_attempts=max_attempts,
        run_after=run_after or _now(),
    )
    # SAVEPOINT (BE-003, audit 2026-09-05): a unique-key collision must unwind
    # THIS row only. This used to `db.rollback()` the whole session — and every
    # `commit=False` caller (email intake, webhook emit inside pay_run …) hands
    # over a session that already holds its real business work, so a collision
    # silently discarded that work while the route went on to commit an empty
    # transaction and answer 2xx. Same pattern as `webhooks.emit`.
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        if idempotency_key is None:
            # Not the idempotency race — an FK or NOT NULL violation. Never
            # answer that with somebody else's job.
            raise
        # A concurrent enqueue won the unique (org, kind, key) race — return theirs.
        winner = await db.scalar(
            select(Job).where(
                Job.org_id == org_id, Job.kind == kind, Job.idempotency_key == idempotency_key
            )
        )
        if winner is None:  # pragma: no cover - the unique conflict guarantees a row
            raise
        if commit:
            await db.commit()
        return winner
    if commit:
        await db.commit()
        await db.refresh(job)
    return job


async def claim(
    db: AsyncSession,
    worker_id: str,
    *,
    kinds: tuple[str, ...] | None = None,
    exclude: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> Job | None:
    """Atomically lease the oldest ready job to `worker_id`, or return None.

    Uses an optimistic guarded UPDATE (WHERE status='queued') so two workers
    that pick the same candidate can't both win — the loser's UPDATE matches
    zero rows and it tries the next candidate. Portable across SQLite/Postgres.

    `kinds` / `exclude` scope a worker to a LANE (resource isolation): a worker
    started with `kinds=('email.extract',)` only leases OCR jobs, so a heavy
    lane can run on its own pool and never starve (or be starved by) another. A
    worker with neither filter drains every kind — the default, unchanged.
    """
    now = _now(now)
    for _ in range(10):  # bounded retries against contention
        stmt = (
            select(Job.id)
            .where(Job.status == jobmodel.QUEUED, Job.run_after <= now)
            .order_by(Job.run_after.asc(), Job.created_at.asc())
            .limit(1)
        )
        if kinds:
            stmt = stmt.where(Job.kind.in_(kinds))
        if exclude:
            stmt = stmt.where(Job.kind.not_in(exclude))
        candidate = await db.scalar(stmt)
        if candidate is None:
            return None
        result = await db.execute(
            update(Job)
            .where(Job.id == candidate, Job.status == jobmodel.QUEUED)
            .values(
                status=jobmodel.RUNNING,
                attempts=Job.attempts + 1,
                locked_at=now,
                locked_by=worker_id,
                updated_at=now,
            )
        )
        if cast(CursorResult, result).rowcount == 1:
            await db.commit()
            return await db.get(Job, candidate)
        await db.rollback()  # lost the race — try the next candidate
    return None


async def _complete(db: AsyncSession, job: Job, result: dict | None) -> None:
    job.status = jobmodel.SUCCEEDED
    job.result_json = json.dumps(result or {})
    job.last_error = None
    job.locked_at = None
    job.locked_by = None
    await db.commit()


async def _fail(db: AsyncSession, job: Job, error: str, *, now: datetime | None = None) -> None:
    now = _now(now)
    job.last_error = error[:2000]
    job.locked_at = None
    job.locked_by = None
    if job.attempts >= job.max_attempts:
        job.status = jobmodel.DEAD
    else:
        job.status = jobmodel.QUEUED
        job.run_after = now + timedelta(seconds=_backoff(job.attempts))
    await db.commit()


async def run_once(
    db: AsyncSession,
    worker_id: str,
    *,
    kinds: tuple[str, ...] | None = None,
    exclude: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> Job | None:
    """Claim and process a single job. Returns the job (in its terminal/retry
    state) or None if the queue was empty. `kinds`/`exclude` scope the lane
    (see `claim`)."""
    job = await claim(db, worker_id, kinds=kinds, exclude=exclude, now=now)
    if job is None:
        return None

    fn = _HANDLERS.get(job.kind)
    if fn is None:
        # A missing handler is a permanent misconfiguration for that kind —
        # retrying within the same deployment can't help, so dead-letter now.
        job.status = jobmodel.DEAD
        job.last_error = f"no handler registered for kind '{job.kind}'"
        job.locked_at = None
        job.locked_by = None
        await db.commit()
        return job

    payload = json.loads(job.payload_json or "{}")
    job_id, org_id, kind = job.id, job.org_id, job.kind
    token = set_current_org(org_id)  # run the handler in its tenant's scope
    try:
        result = await fn(db, payload, job)
        await _complete(db, job, result)
    except Exception as exc:  # noqa: BLE001 — any handler error retries/dead-letters
        # A rollback expires every attribute on `job`, so reload it before
        # touching attempts/status (accessing an expired attr would do sync IO).
        await db.rollback()
        job = await db.get(Job, job_id)
        assert job is not None  # the row exists — we loaded it moments ago
        log.warning("job %s (%s) failed on attempt %s: %s", job_id, kind, job.attempts, exc)
        await _fail(db, job, f"{type(exc).__name__}: {exc}", now=now)
    finally:
        reset_current_org(token)
    return job


LEASE_EXPIRED_ERROR = "lease expired: the worker died mid-run"


async def reclaim_stale(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Return jobs with an expired lease (crashed worker) to the queue — or to
    the dead-letter state once they have used their attempts.

    BE-002 (audit 2026-09-05): the attempt counter is incremented on CLAIM, but
    this used to requeue every stale lease unconditionally and immediately. A
    job that KILLS its worker (OOM in OCR, a native-parser crash) never reaches
    `_fail`'s dead-letter branch, so it was reclaimed, re-run, killed the next
    worker, and repeated forever — starving every other job in its lane. Now a
    stale lease at `max_attempts` dead-letters with a named reason, and one
    with attempts left backs off like an ordinary failure instead of hot-looping.
    Returns the number of leases touched (requeued + dead-lettered)."""
    now = _now(now)
    cutoff = now - timedelta(seconds=STALE_LEASE_SECONDS)
    stale = list(
        await db.scalars(
            select(Job).where(
                Job.status == jobmodel.RUNNING,
                or_(Job.locked_at.is_(None), Job.locked_at < cutoff),
            )
        )
    )
    for job in stale:
        job.locked_at = None
        job.locked_by = None
        job.updated_at = now
        if job.attempts >= job.max_attempts:
            job.status = jobmodel.DEAD
            job.last_error = LEASE_EXPIRED_ERROR
        else:
            job.status = jobmodel.QUEUED
            job.last_error = LEASE_EXPIRED_ERROR
            job.run_after = now + timedelta(seconds=_backoff(job.attempts))
    await db.commit()
    return len(stale)


class JobNotRetryable(Exception):
    """`retry` was asked to requeue a job that is not dead, failed or waiting."""


# The statuses `retry` may act on. RUNNING is the one that matters (BE-001,
# audit 2026-09-05): clearing `locked_by` on a job a worker is executing makes
# it claimable by a second worker, so the SAME job runs twice in parallel —
# for `everypay.charge_mit` that is a double card charge. SUCCEEDED is refused
# because re-running finished work is a new job, not a retry.
RETRYABLE_STATUSES = frozenset({jobmodel.DEAD, jobmodel.FAILED, jobmodel.QUEUED})


async def retry(db: AsyncSession, job: Job, *, now: datetime | None = None) -> Job:
    """Manually requeue a dead/failed job (resets the attempt counter).
    Refuses a RUNNING or SUCCEEDED job — see `RETRYABLE_STATUSES`."""
    if job.status not in RETRYABLE_STATUSES:
        raise JobNotRetryable(
            f"job {job.id} is {job.status}; only "
            f"{', '.join(sorted(RETRYABLE_STATUSES))} jobs can be retried"
        )
    job.status = jobmodel.QUEUED
    job.attempts = 0
    job.run_after = _now(now)
    job.last_error = None
    job.locked_at = None
    job.locked_by = None
    await db.commit()
    await db.refresh(job)
    return job
