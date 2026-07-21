"""Background-queue health + SLO (observability).

A platform-wide (cross-tenant) snapshot of the durable job queue: counts by
status, the **dead-letter depth** (exhausted-retry poison jobs), and the age of
the **oldest ready-but-unprocessed** job — the queue-lag SLO signal. Used by the
`/health/queue` probe (503 when the SLO is breached, so an uptime check pages)
and to refresh the Prometheus gauges (worker keeps them warm each loop).

Runs UNSCOPED by design — this is an operational view across all tenants, and it
never returns any tenant identifier or payload, only aggregate numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.config import settings
from app.models import job as jobmodel
from app.models.job import Job

_ALL_STATUS = (jobmodel.QUEUED, jobmodel.RUNNING, jobmodel.FAILED,
               jobmodel.SUCCEEDED, jobmodel.DEAD)
_PENDING = (jobmodel.QUEUED, jobmodel.FAILED)


@dataclass
class QueueHealth:
    counts: dict[str, int] = field(default_factory=dict)
    dead: int = 0
    pending: int = 0
    oldest_pending_seconds: int = 0
    slo_ok: bool = True


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def snapshot(db: AsyncSession, *, now: datetime | None = None) -> QueueHealth:
    now = now or datetime.now(timezone.utc)

    counts = {s: 0 for s in _ALL_STATUS}
    rows = await db.execute(select(Job.status, func.count()).group_by(Job.status))
    for st, n in rows.all():
        counts[st] = int(n)

    dead = counts.get(jobmodel.DEAD, 0)
    pending = counts.get(jobmodel.QUEUED, 0) + counts.get(jobmodel.FAILED, 0)

    # Oldest job that is READY (run_after has arrived) but still pending.
    oldest_run_after = await db.scalar(
        select(func.min(Job.run_after)).where(
            Job.status.in_(_PENDING), Job.run_after <= now,
        )
    )
    oldest_pending = int((now - _aware(oldest_run_after)).total_seconds()) if oldest_run_after else 0

    slo_ok = (oldest_pending <= settings.queue_slo_max_pending_age_seconds
              and dead <= settings.queue_dlq_alert_threshold)

    metrics.set_queue_metrics(counts, oldest_pending)
    return QueueHealth(counts=counts, dead=dead, pending=pending,
                       oldest_pending_seconds=oldest_pending, slo_ok=slo_ok)
