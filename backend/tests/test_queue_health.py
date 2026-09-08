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


# --- dead-letter depth by kind (reference R2 review → ops group) -------------


@pytest.mark.asyncio
async def test_snapshot_breaks_the_dead_letter_depth_down_by_kind(auth_client, db_session):
    """`dead` says that something dead-lettered; `dead_by_kind` says WHAT — a
    dead `billing.apply_subscription_event` is a customer off their plan, a
    dead webhook delivery is a receiver that is down. Kinds only: no tenant
    id, no payload."""
    org_id = await _org_id(db_session)
    for kind in ("billing.apply_subscription_event", "webhook.deliver", "webhook.deliver"):
        db_session.add(
            Job(
                org_id=org_id,
                kind=kind,
                payload_json='{"secret": "never-exposed"}',
                status=jobmodel.DEAD,
                attempts=5,
                max_attempts=5,
                run_after=NOW - timedelta(hours=1),
            )
        )
    await _job(db_session, org_id, status=jobmodel.QUEUED, run_after=NOW - timedelta(minutes=1))
    h = await queue_health.snapshot(db_session, now=NOW)
    assert h.dead == 3
    assert h.dead_by_kind == {"billing.apply_subscription_event": 1, "webhook.deliver": 2}
    assert "never-exposed" not in repr(h)


@pytest.mark.asyncio
async def test_nothing_dead_means_an_empty_breakdown_and_no_query(auth_client, db_session):
    """The breakdown is a third statement only when something is dead — the
    snapshot runs on every worker tick, so the common case stays at two."""
    from sqlalchemy import event

    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    bind = db_session.sync_session.get_bind()
    event.listen(bind, "before_cursor_execute", _count)
    try:
        h = await queue_health.snapshot(db_session, now=NOW)
        assert h.dead == 0 and h.dead_by_kind == {}
        assert len(statements) == 2, statements
        org_id = await _org_id(db_session)
        await _job(db_session, org_id, status=jobmodel.DEAD, run_after=NOW - timedelta(hours=1))
        statements.clear()
        h = await queue_health.snapshot(db_session, now=NOW)
        assert h.dead_by_kind == {"test.job": 1}
        assert len(statements) == 3, statements
    finally:
        event.remove(bind, "before_cursor_execute", _count)


@pytest.mark.asyncio
async def test_probe_body_carries_the_breakdown(auth_client, db_session):
    org_id = await _org_id(db_session)
    db_session.add(
        Job(
            org_id=org_id,
            kind="webhook.deliver",
            payload_json="{}",
            status=jobmodel.DEAD,
            attempts=5,
            max_attempts=5,
            run_after=datetime.now(UTC),
        )
    )
    await db_session.commit()
    r = await auth_client.get("/health/queue")
    assert r.status_code == 503
    assert r.json()["dead_by_kind"] == {"webhook.deliver": 1}


def test_the_dead_gauge_is_labelled_by_kind_and_a_kind_that_recovers_reads_zero():
    """A labelled Prometheus gauge keeps its last value until it is set again.
    Without re-publishing 0 for kinds absent from the current snapshot, a
    kind whose dead jobs were requeued would page forever."""
    from app.core import metrics

    pytest.importorskip("prometheus_client")
    from prometheus_client import REGISTRY

    def dead(kind):
        return REGISTRY.get_sample_value("invoiceiq_jobs_dead", {"kind": kind})

    metrics.set_queue_metrics({"dead": 3}, 0, {"webhook.deliver": 2, "email.extract": 1})
    assert dead("webhook.deliver") == 2 and dead("email.extract") == 1
    assert REGISTRY.get_sample_value("invoiceiq_jobs", {"status": "dead"}) == 3
    metrics.set_queue_metrics({"dead": 1}, 0, {"email.extract": 1})
    assert dead("webhook.deliver") == 0, "a recovered kind must read 0, not its old count"
    assert dead("email.extract") == 1
    metrics.set_queue_metrics({"dead": 0}, 0, {})
    assert dead("email.extract") == 0
    # Without a breakdown (the snapshot's own call always passes one) the
    # kind gauge is left alone and the status gauge still moves.
    metrics.set_queue_metrics({"dead": 7}, 0)
    assert dead("email.extract") == 0 and dead("webhook.deliver") == 0
    assert REGISTRY.get_sample_value("invoiceiq_jobs", {"status": "dead"}) == 7
