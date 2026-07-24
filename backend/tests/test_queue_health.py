"""Background-queue health + SLO (observability): dead-letter depth, queue-lag
age, the slo_ok flag, and the /health/queue probe (503 when degraded)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import job as jobmodel
from app.models.job import Job
from app.models.organization import Organization
from app.services import queue_health

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


async def _org_id(db):
    return await db.scalar(select(Organization.id))


async def _job(db, org_id, *, status, run_after):
    db.add(
        Job(
            org_id=org_id,
            kind="test.job",
            payload_json="{}",
            status=status,
            attempts=0,
            max_attempts=5,
            run_after=run_after,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_snapshot_counts_and_lag(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _job(db_session, org_id, status=jobmodel.QUEUED, run_after=NOW - timedelta(minutes=5))
    await _job(db_session, org_id, status=jobmodel.QUEUED, run_after=NOW - timedelta(minutes=1))
    await _job(db_session, org_id, status=jobmodel.SUCCEEDED, run_after=NOW - timedelta(hours=1))

    h = await queue_health.snapshot(db_session, now=NOW)
    assert h.counts[jobmodel.QUEUED] == 2 and h.counts[jobmodel.SUCCEEDED] == 1
    assert h.pending == 2 and h.dead == 0
    assert h.oldest_pending_seconds == 300  # the 5-min-old queued job
    assert h.slo_ok is True


@pytest.mark.asyncio
async def test_future_jobs_do_not_count_as_lag(auth_client, db_session):
    org_id = await _org_id(db_session)
    # Scheduled for the future → not "behind".
    await _job(db_session, org_id, status=jobmodel.QUEUED, run_after=NOW + timedelta(hours=1))
    h = await queue_health.snapshot(db_session, now=NOW)
    assert h.oldest_pending_seconds == 0 and h.slo_ok is True


@pytest.mark.asyncio
async def test_dead_letter_breaches_slo(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _job(db_session, org_id, status=jobmodel.DEAD, run_after=NOW - timedelta(hours=2))
    h = await queue_health.snapshot(db_session, now=NOW)
    assert h.dead == 1 and h.slo_ok is False  # any dead job breaches (default threshold 0)


@pytest.mark.asyncio
async def test_stale_pending_breaches_slo(auth_client, db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "queue_slo_max_pending_age_seconds", 600)
    org_id = await _org_id(db_session)
    await _job(db_session, org_id, status=jobmodel.QUEUED, run_after=NOW - timedelta(minutes=30))
    h = await queue_health.snapshot(db_session, now=NOW)
    assert h.oldest_pending_seconds == 1800 and h.slo_ok is False


# --- probe -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_ok_when_empty(auth_client):
    r = await auth_client.get("/health/queue")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["dead"] == 0


@pytest.mark.asyncio
async def test_probe_503_when_dead_present(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _job(db_session, org_id, status=jobmodel.DEAD, run_after=datetime.now(UTC))
    r = await auth_client.get("/health/queue")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded" and r.json()["dead"] == 1
