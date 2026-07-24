"""Durable job queue: enqueue, atomic claim, retries with backoff, dead-letter,
idempotency, stale-lease reclaim, and tenant isolation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import job as jobmodel
from app.models.job import Job
from app.models.organization import Organization
from app.services import jobs


async def _org(db_session) -> str:
    return await db_session.scalar(select(Organization.id).limit(1))


@pytest.fixture(autouse=True)
def _clean_handlers():
    """Snapshot the handler registry so test handlers don't leak."""
    saved = dict(jobs._HANDLERS)
    yield
    jobs._HANDLERS.clear()
    jobs._HANDLERS.update(saved)


@pytest.mark.asyncio
async def test_enqueue_claim_run_success(auth_client, db_session):
    org = await _org(db_session)
    seen = []

    @jobs.handler("test.echo")
    async def _echo(db, payload, job):
        seen.append(payload)
        return {"ok": True, "n": payload.get("n")}

    await jobs.enqueue(db_session, "test.echo", {"n": 7}, org_id=org)
    job = await jobs.run_once(db_session, "w1")
    assert job is not None
    assert job.status == jobmodel.SUCCEEDED
    assert job.attempts == 1
    assert seen == [{"n": 7}]
    assert '"n": 7' in (job.result_json or "") or '"n":7' in (job.result_json or "")

    # Queue now empty.
    assert await jobs.run_once(db_session, "w1") is None


@pytest.mark.asyncio
async def test_idempotency_key_dedupes(auth_client, db_session):
    org = await _org(db_session)
    a = await jobs.enqueue(
        db_session, "recurring.generate", {}, org_id=org, idempotency_key="daily"
    )
    b = await jobs.enqueue(
        db_session, "recurring.generate", {}, org_id=org, idempotency_key="daily"
    )
    assert a.id == b.id
    count = await db_session.scalar(
        select(jobs.Job.id).where(Job.org_id == org, Job.idempotency_key == "daily")
    )
    assert count is not None
    rows = list(await db_session.scalars(select(Job).where(Job.idempotency_key == "daily")))
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_failure_retries_with_backoff_then_dead(auth_client, db_session):
    org = await _org(db_session)

    @jobs.handler("test.boom")
    async def _boom(db, payload, job):
        raise RuntimeError("kaboom")

    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    await jobs.enqueue(db_session, "test.boom", {}, org_id=org, max_attempts=2, run_after=now)

    job = await jobs.run_once(db_session, "w1", now=now)
    # First failure → requeued for a future retry, not yet dead.
    assert job.status == jobmodel.QUEUED
    assert job.attempts == 1
    assert job.run_after > now
    assert "kaboom" in job.last_error

    # Not yet due at `now` → claim returns nothing.
    assert await jobs.run_once(db_session, "w1", now=now) is None

    # After the backoff window, the second (final) attempt dead-letters it.
    later = job.run_after + timedelta(seconds=1)
    job2 = await jobs.run_once(db_session, "w1", now=later)
    assert job2.id == job.id
    assert job2.status == jobmodel.DEAD
    assert job2.attempts == 2


@pytest.mark.asyncio
async def test_unknown_kind_dead_letters(auth_client, db_session):
    org = await _org(db_session)
    await jobs.enqueue(db_session, "test.nohandler", {}, org_id=org, max_attempts=3)
    job = await jobs.run_once(db_session, "w1")
    assert job.status == jobmodel.DEAD
    assert "no handler" in job.last_error


@pytest.mark.asyncio
async def test_claim_is_exclusive(auth_client, db_session):
    """Two claims never return the same job."""
    org = await _org(db_session)

    @jobs.handler("test.slow")
    async def _noop(db, payload, job):
        return {}

    await jobs.enqueue(db_session, "test.slow", {"i": 1}, org_id=org)
    await jobs.enqueue(db_session, "test.slow", {"i": 2}, org_id=org)

    j1 = await jobs.claim(db_session, "w1")
    j2 = await jobs.claim(db_session, "w2")
    j3 = await jobs.claim(db_session, "w3")
    assert j1 is not None and j2 is not None
    assert j1.id != j2.id  # distinct jobs
    assert j3 is None  # only two were queued


@pytest.mark.asyncio
async def test_reclaim_stale_lease(auth_client, db_session):
    org = await _org(db_session)
    j = await jobs.enqueue(db_session, "recurring.generate", {}, org_id=org)
    # Simulate a crashed worker: claimed long ago, still 'running'.
    claimed = await jobs.claim(db_session, "dead-worker")
    claimed.locked_at = datetime.now(UTC) - timedelta(seconds=jobs.STALE_LEASE_SECONDS + 60)
    await db_session.commit()

    reclaimed = await jobs.reclaim_stale(db_session)
    assert reclaimed == 1
    refreshed = await db_session.get(Job, j.id)
    assert refreshed.status == jobmodel.QUEUED


@pytest.mark.asyncio
async def test_recurring_handler_generates_invoices(auth_client, db_session):
    """The real recurring.generate handler emits invoices when run via the queue."""
    # Activate issuing + a due schedule through the API.
    issuer = {
        "legal_name": "InvoiceIQ Demo BV",
        "vat_number": "NL123456789B01",
        "registration_number": "NL-KVK-12345678",
        "address_line1": "Keizersgracht 1",
        "city": "Amsterdam",
        "postal_code": "1015 CJ",
        "country": "NL",
        "email": "b@i.test",
    }
    assert (await auth_client.put("/api/v1/issuer", json=issuer)).status_code == 200
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    await auth_client.post(
        "/api/v1/issued/recurring",
        json={
            "template": {
                "buyer_name": "Initech",
                "lines": [
                    {
                        "description": "Retainer",
                        "quantity": "1",
                        "unit_price": "100",
                        "vat_rate": "21",
                    }
                ],
            },
            "frequency": "monthly",
            "start_date": "2020-01-01",
            "end_date": "2020-01-15",
        },
    )

    org = await _org(db_session)
    await jobs.enqueue(db_session, "recurring.generate", {}, org_id=org)
    job = await jobs.run_once(db_session, "w1")
    assert job.status == jobmodel.SUCCEEDED
    assert '"generated": 1' in job.result_json


@pytest.mark.asyncio
async def test_jobs_api_enqueue_list_and_isolation(auth_client, client):
    # Owner can enqueue an allowlisted kind.
    r = await auth_client.post("/api/v1/jobs", json={"kind": "dunning.run"})
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]
    assert r.json()["status"] == "queued"

    # A non-allowlisted kind is refused.
    bad = await auth_client.post("/api/v1/jobs", json={"kind": "test.boom"})
    assert bad.status_code == 400

    # Visible to its own tenant.
    lst = (await auth_client.get("/api/v1/jobs")).json()
    assert any(j["id"] == job_id for j in lst)

    # A second tenant sees none of it.
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Co",
            "name": "O",
            "email": "o@o.io",
            "password": "supersecret2",
        },
    )
    client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"
    assert (await client.get("/api/v1/jobs")).json() == []
    assert (await client.get(f"/api/v1/jobs/{job_id}")).status_code == 404


@pytest.mark.asyncio
async def test_scheduler_enqueues_daily_jobs_idempotently(auth_client, db_session):
    from datetime import date

    from app.services import scheduler

    today = date(2026, 7, 20)
    created = await scheduler.enqueue_daily(db_session, today=today)
    # One org × the daily kinds (recurring-generate, dunning-run, ap-due-alerts).
    n = len(scheduler.DAILY_KINDS)
    assert created == n
    # Running again the same day adds nothing (idempotent per date).
    assert await scheduler.enqueue_daily(db_session, today=today) == 0
    # A new day enqueues fresh jobs.
    assert await scheduler.enqueue_daily(db_session, today=date(2026, 7, 21)) == n


@pytest.mark.asyncio
async def test_jobs_api_enqueue_requires_admin(auth_client, client, db_session):
    """A read-only user cannot enqueue jobs."""
    # Invite + accept a low-privilege user in the same org via direct role set.
    from sqlalchemy import update as _update

    from app.models.user import User, UserRole

    # Downgrade a freshly-registered second account is complex; instead assert the
    # allowlist + admin gate by flipping the current user's role to 'user'.
    org = await _org(db_session)
    await db_session.execute(_update(User).where(User.org_id == org).values(role=UserRole.user))
    await db_session.commit()

    r = await auth_client.post("/api/v1/jobs", json={"kind": "dunning.run"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_lane_kinds_only_claims_matching(auth_client, db_session):
    """A worker scoped with `kinds` leases only that lane; other kinds are left
    for another pool (resource isolation for OCR/heavy modules)."""
    org = await _org(db_session)

    @jobs.handler("email.extract")
    async def _extract(db, payload, job):
        return {"lane": "extract"}

    @jobs.handler("dunning.run")
    async def _dunning(db, payload, job):
        return {"lane": "general"}

    await jobs.enqueue(db_session, "dunning.run", {}, org_id=org)
    await jobs.enqueue(db_session, "email.extract", {}, org_id=org)

    # The extract lane only ever claims email.extract, even though dunning is
    # older/ready — it is invisible to this worker.
    job = await jobs.run_once(db_session, "extract-worker", kinds=("email.extract",))
    assert job is not None and job.kind == "email.extract"
    # Nothing else in this lane.
    assert await jobs.run_once(db_session, "extract-worker", kinds=("email.extract",)) is None
    # The dunning job is still queued for the general pool.
    remaining = await db_session.scalar(
        select(Job.status).where(Job.kind == "dunning.run", Job.org_id == org)
    )
    assert remaining == jobmodel.QUEUED


@pytest.mark.asyncio
async def test_lane_exclude_skips_kind(auth_client, db_session):
    """The complementary lane (`exclude`) drains everything BUT the heavy kind."""
    org = await _org(db_session)

    @jobs.handler("email.extract")
    async def _extract(db, payload, job):
        return {}

    @jobs.handler("dunning.run")
    async def _dunning(db, payload, job):
        return {}

    await jobs.enqueue(db_session, "email.extract", {}, org_id=org)
    await jobs.enqueue(db_session, "dunning.run", {}, org_id=org)

    job = await jobs.run_once(db_session, "general", exclude=("email.extract",))
    assert job is not None and job.kind == "dunning.run"
    # email.extract is never claimed by the general lane.
    assert await jobs.run_once(db_session, "general", exclude=("email.extract",)) is None
    left = await db_session.scalar(
        select(Job.status).where(Job.kind == "email.extract", Job.org_id == org)
    )
    assert left == jobmodel.QUEUED
