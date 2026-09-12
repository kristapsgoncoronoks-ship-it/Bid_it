"""BE-024 — the per-job deadline.

STIR-P1-01 gave a running job a lease heartbeat so a SLOW job would stop being
mistaken for a CRASHED one. The cost, found by the R6 panels: a HUNG job renews
its lease for ever, so `reclaim_stale` leaves it alone and the R6 liveness probe
stays green (the heartbeat is what touches the probe file). One wedged handler
silently removed a worker from the fleet.

These tests pin the bound and the things it must NOT break: a timed-out job
RETRIES rather than dead-lettering on the first attempt, the tenant scope and
log context are still unwound, and a kind that legitimately runs long keeps its
own budget.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import job as jobmodel
from app.models.job import Job
from app.models.organization import Organization
from app.services import job_handlers, jobs


async def _org(db_session) -> str:
    return await db_session.scalar(select(Organization.id).limit(1))


@pytest.fixture(autouse=True)
def _clean_handlers():
    saved = dict(jobs._HANDLERS)
    yield
    jobs._HANDLERS.clear()
    jobs._HANDLERS.update(saved)


@pytest.mark.asyncio
async def test_a_hung_handler_is_cancelled_at_its_deadline_and_retries(auth_client, db_session):
    """The headline: a handler that never returns does not hold the worker.

    It fails like any other failure — queued again with a backoff — because a
    deadline is evidence about this attempt, not a verdict on the job."""
    org = await _org(db_session)
    entered = asyncio.Event()

    @jobs.handler("test.hangs", deadline_seconds=0.05)
    async def _hangs(db, payload, job):
        entered.set()
        await asyncio.Event().wait()  # never resolves
        raise AssertionError("unreachable")  # pragma: no cover

    await jobs.enqueue(db_session, "test.hangs", {}, org_id=org)
    job = await asyncio.wait_for(jobs.run_once(db_session, "w1"), timeout=10)

    assert entered.is_set(), "the handler must actually have started"
    assert job is not None
    assert job.status == jobmodel.QUEUED, "a deadline retries; it does not dead-letter at once"
    assert job.attempts == 1
    assert "deadline" in (job.last_error or "")
    assert "test.hangs" in (job.last_error or "")
    # The lease must be released, or the retry cannot be claimed by anyone.
    assert job.locked_by is None and job.locked_at is None


@pytest.mark.asyncio
async def test_the_deadline_does_not_fire_on_a_handler_that_finishes(auth_client, db_session):
    """The falsifiable other half: the bound must not truncate honest work."""
    org = await _org(db_session)

    @jobs.handler("test.quick", deadline_seconds=5.0)
    async def _quick(db, payload, job):
        await asyncio.sleep(0.01)
        return {"ok": True}

    await jobs.enqueue(db_session, "test.quick", {}, org_id=org)
    job = await jobs.run_once(db_session, "w1")
    assert job is not None and job.status == jobmodel.SUCCEEDED
    assert job.last_error is None


@pytest.mark.asyncio
async def test_a_timed_out_job_dead_letters_once_it_runs_out_of_attempts(auth_client, db_session):
    """A handler that hangs EVERY time still terminates: retry, retry, dead.

    Without this the bound would only convert one silent failure (a wedged
    worker) into another (a job that retries for ever)."""
    org = await _org(db_session)

    @jobs.handler("test.always_hangs", deadline_seconds=0.02)
    async def _always(db, payload, job):
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    await jobs.enqueue(db_session, "test.always_hangs", {}, org_id=org, max_attempts=2)
    for _ in range(2):
        job = await asyncio.wait_for(jobs.run_once(db_session, "w1"), timeout=10)
        assert job is not None, "the job must be claimable on every attempt"
        # Undo the backoff so the next attempt is ready now (`run_after` is NOT
        # NULL, so it is backdated rather than cleared).
        await db_session.execute(
            jobmodel.Job.__table__.update()
            .where(jobmodel.Job.__table__.c.id == job.id)
            .values(run_after=datetime.now(UTC) - timedelta(hours=1))
        )
        await db_session.commit()

    row = await db_session.get(Job, job.id)
    await db_session.refresh(row)
    assert row.status == jobmodel.DEAD, "an always-hanging job must reach the dead-letter state"
    assert row.attempts == 2


@pytest.mark.asyncio
async def test_the_tenant_scope_and_job_context_survive_a_deadline(auth_client, db_session):
    """A cancellation unwinds through the same `finally` as any other failure.

    If it did not, the NEXT job on this worker would run inside the timed-out
    job's tenant scope — a cross-tenant write with no attacker involved."""
    from app.core.tenant import get_current_org

    org = await _org(db_session)

    @jobs.handler("test.hangs2", deadline_seconds=0.05)
    async def _hangs(db, payload, job):
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    await jobs.enqueue(db_session, "test.hangs2", {}, org_id=org)
    await asyncio.wait_for(jobs.run_once(db_session, "w1"), timeout=10)
    assert get_current_org() is None, "the tenant scope must be reset after a deadline"


def test_the_default_budget_applies_to_a_kind_that_declares_none():
    @jobs.handler("test.undeclared")
    async def _plain(db, payload, job):  # pragma: no cover - never run
        return {}

    assert jobs.deadline_for("test.undeclared") == float(settings.job_deadline_seconds)
    # An unregistered kind still answers, so `run_once` can never read None.
    assert jobs.deadline_for("kind.that.does.not.exist") == float(settings.job_deadline_seconds)


def test_a_declared_budget_wins_over_the_default():
    @jobs.handler("test.declared", deadline_seconds=12.5)
    async def _declared(db, payload, job):  # pragma: no cover - never run
        return {}

    assert jobs.deadline_for("test.declared") == 12.5


def test_a_nonsense_budget_is_refused_at_registration():
    """A zero or NaN budget would cancel every job of that kind instantly, and
    the failure would look like a broken handler rather than a typo here."""
    for bad in (0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite and positive"):

            @jobs.handler("test.bad", deadline_seconds=bad)
            async def _bad(db, payload, job):  # pragma: no cover - never registered
                return {}


def test_every_long_running_kind_declares_a_budget_above_the_default():
    """The five kinds whose cost scales with a tenant's whole history — not with
    a fixed unit of work — must not be bounded by the ordinary default.

    This is the staleness guard: a new export or sweep kind added without a
    budget reads as an ordinary handler and gets 30 minutes, which is the bug
    this list exists to make visible."""
    long_kinds = (
        job_handlers.ARCHIVE_EXPORT,
        job_handlers.WORKSPACE_EXPORT,
        job_handlers.INTEGRITY_VERIFY,
        job_handlers.RECEIPT_CONTROL_RUN,
        job_handlers.PLATFORM_BILLING_RUN,
    )
    default = float(settings.job_deadline_seconds)
    for kind in long_kinds:
        assert kind in jobs._HANDLERS, f"{kind} is not registered"
        declared = jobs._HANDLERS[kind].deadline_seconds
        assert declared is not None, f"{kind} must declare its own deadline"
        assert declared > default, f"{kind}'s budget must exceed the ordinary default"


def test_every_registered_kind_has_a_usable_budget():
    """No kind may end up with a budget that is absent, zero or not finite."""
    import math

    for kind in jobs.registered_kinds():
        budget = jobs.deadline_for(kind)
        assert math.isfinite(budget) and budget > 0, f"{kind} has an unusable budget: {budget!r}"
