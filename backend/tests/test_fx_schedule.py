"""WO-8 — the scheduled daily ECB refresh.

ECB rates are global reference data: the scheduler enqueues the refresh per org
(the queue is tenant-keyed) and the HANDLER dedupes on the shared per-day
idempotency key, so the feed is fetched once per day, not once per tenant. A
failed fetch never raises into the scheduler and cached rates keep serving.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.models import job as jobmodel
from app.models.job import Job
from app.services import fx, job_handlers, jobs, scheduler

_TODAY = date(2026, 7, 20)


@pytest.mark.asyncio
async def test_daily_fx_refresh_is_enqueued_once_per_day(auth_client, db_session):
    for _ in range(100):
        await scheduler.enqueue_daily(db_session, today=_TODAY)
    rows = list(
        await db_session.scalars(
            select(Job).where(
                Job.kind == job_handlers.FX_REFRESH,
                Job.idempotency_key == f"{job_handlers.FX_REFRESH}:{_TODAY.isoformat()}",
            )
        )
    )
    assert len(rows) == 1  # one org → exactly one live FX job for the day
    # A new day enqueues a fresh one.
    await scheduler.enqueue_daily(db_session, today=date(2026, 7, 21))
    total = len(
        list(await db_session.scalars(select(Job).where(Job.kind == job_handlers.FX_REFRESH)))
    )
    assert total == 2


@pytest.mark.asyncio
async def test_fx_refresh_handler_never_raises_into_the_scheduler(
    auth_client, db_session, monkeypatch
):
    def _boom(url, timeout=12):
        raise OSError("network blocked")

    monkeypatch.setattr(fx, "_fetch", _boom)
    await scheduler.enqueue_daily(db_session, today=_TODAY)
    job = await jobs.run_once(db_session, "w1", kinds=(job_handlers.FX_REFRESH,))
    assert job is not None
    # The failure is RECORDED (ok=false), not raised — the job completes and the
    # scheduler/worker loop continues undisturbed.
    assert job.status == jobmodel.SUCCEEDED
    assert '"ok": false' in (job.result_json or "")
    assert "OSError" in (job.result_json or "")


@pytest.mark.asyncio
async def test_cached_rates_still_serve_after_a_failed_refresh(
    auth_client, db_session, monkeypatch
):
    before = await fx.rate_for(db_session, "USD", date(2026, 7, 18))
    assert before is not None  # conftest seeded the bundled snapshot

    def _boom(url, timeout=12):
        raise OSError("network blocked")

    monkeypatch.setattr(fx, "_fetch", _boom)
    res = await fx.refresh_from_ecb(db_session)
    assert res["ok"] is False
    after = await fx.rate_for(db_session, "USD", date(2026, 7, 18))
    assert after == before  # graceful degradation: the cache keeps serving


@pytest.mark.asyncio
async def test_fx_refresh_fetches_once_across_tenants(auth_client, client, db_session, monkeypatch):
    """Two tenants, one feed: ECB rates are global reference data, so exactly ONE
    fx.refresh job exists per day — carried by a single org — and the feed is
    fetched once, never once per tenant."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Co",
            "name": "O",
            "email": "o@other.io",
            "password": "supersecret2",
        },
    )
    assert reg.status_code == 201

    calls = {"n": 0}

    async def _fake_refresh(db, history=True):
        calls["n"] += 1
        return {"ok": True, "written": 3}

    monkeypatch.setattr(fx, "refresh_from_ecb", _fake_refresh)
    await scheduler.enqueue_daily(db_session, today=_TODAY)

    rows = list(await db_session.scalars(select(Job).where(Job.kind == job_handlers.FX_REFRESH)))
    assert len(rows) == 1  # two orgs, ONE global job

    first = await jobs.run_once(db_session, "w1", kinds=(job_handlers.FX_REFRESH,))
    assert first is not None and first.status == jobmodel.SUCCEEDED
    assert calls["n"] == 1  # the ECB feed was fetched ONCE, not once per tenant
    # Nothing left in the lane.
    assert await jobs.run_once(db_session, "w1", kinds=(job_handlers.FX_REFRESH,)) is None
