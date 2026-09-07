"""Metered-usage reporting to Stripe (Phase 3.9 continuation, ADR-0013):
frozen retry-safe segments (BILL-METER-001), provider/meter gating, scheduler."""

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.models.usage import UsageCounter
from app.services import billing_usage
from app.services.access import _period
from app.services.billing_provider import BillingError, NullProvider, set_billing_provider


class FakeStripe:
    kind = "subscription"
    name = "stripe"
    enabled = True

    def __init__(self):
        self.calls = []

    async def report_usage(self, *, customer_id, meter_event, quantity, identifier):
        self.calls.append(
            {
                "customer_id": customer_id,
                "meter_event": meter_event,
                "quantity": quantity,
                "identifier": identifier,
            }
        )


class FakeEveryPay:
    kind = "redirect"
    name = "everypay"
    enabled = True


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_billing_provider(None)


async def _org(db):
    org = await db.scalar(select(Organization))
    org.stripe_customer_id = "cus_x"
    await db.commit()
    return org


async def _seed_usage(db, org_id, count, reported=0, reporting_target=None):
    db.add(
        UsageCounter(
            org_id=org_id,
            period=_period(),
            metric="upload",
            count=count,
            reported=reported,
            reporting_target=reported if reporting_target is None else reporting_target,
        )
    )
    await db.commit()


class AcceptThenLoseResponseStripe(FakeStripe):
    """The first request reaches the provider, but our client never sees its
    success (timeout, connection reset after the write, worker killed)."""

    def __init__(self):
        super().__init__()
        self.lose_next_response = True

    async def report_usage(self, *, customer_id, meter_event, quantity, identifier):
        await super().report_usage(
            customer_id=customer_id,
            meter_event=meter_event,
            quantity=quantity,
            identifier=identifier,
        )
        if self.lose_next_response:
            self.lose_next_response = False
            raise BillingError("simulated response lost after provider acceptance")


def _map_meter(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_meter_upload", "uploads_meter")


# --- delta reporting -------------------------------------------------------


@pytest.mark.asyncio
async def test_reports_delta_and_advances_watermark(auth_client, db_session, monkeypatch):
    _map_meter(monkeypatch)
    org = await _org(db_session)
    await _seed_usage(db_session, org.id, count=10)
    fake = FakeStripe()
    set_billing_provider(fake)

    res = await billing_usage.report_org_usage(db_session, org.id)
    assert res == {"upload": 10}
    assert len(fake.calls) == 1 and fake.calls[0]["quantity"] == 10
    assert fake.calls[0]["customer_id"] == "cus_x"

    counter = await db_session.scalar(select(UsageCounter))
    assert counter.reported == 10


@pytest.mark.asyncio
async def test_second_run_is_noop_until_usage_grows(auth_client, db_session, monkeypatch):
    _map_meter(monkeypatch)
    org = await _org(db_session)
    await _seed_usage(db_session, org.id, count=10)
    fake = FakeStripe()
    set_billing_provider(fake)

    await billing_usage.report_org_usage(db_session, org.id)  # reports 10
    assert await billing_usage.report_org_usage(db_session, org.id) == {}  # delta 0
    assert len(fake.calls) == 1

    counter = await db_session.scalar(select(UsageCounter))
    counter.count = 15
    await db_session.commit()
    assert await billing_usage.report_org_usage(db_session, org.id) == {"upload": 5}
    assert len(fake.calls) == 2 and fake.calls[1]["quantity"] == 5


@pytest.mark.asyncio
async def test_identifier_is_deterministic(auth_client, db_session, monkeypatch):
    _map_meter(monkeypatch)
    org = await _org(db_session)
    await _seed_usage(db_session, org.id, count=7)
    fake = FakeStripe()
    set_billing_provider(fake)
    await billing_usage.report_org_usage(db_session, org.id)
    assert fake.calls[0]["identifier"].endswith(":upload:7")


# --- gating ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_without_meter_mapping(auth_client, db_session):
    org = await _org(db_session)
    await _seed_usage(db_session, org.id, count=10)
    set_billing_provider(FakeStripe())  # meter not mapped
    assert await billing_usage.report_org_usage(db_session, org.id) == {}


@pytest.mark.asyncio
async def test_noop_for_non_stripe_provider(auth_client, db_session, monkeypatch):
    _map_meter(monkeypatch)
    org = await _org(db_session)
    await _seed_usage(db_session, org.id, count=10)
    set_billing_provider(FakeEveryPay())
    assert await billing_usage.report_org_usage(db_session, org.id) == {}


@pytest.mark.asyncio
async def test_noop_without_stripe_customer(auth_client, db_session, monkeypatch):
    _map_meter(monkeypatch)
    org = await db_session.scalar(select(Organization))  # no stripe_customer_id
    await _seed_usage(db_session, org.id, count=10)
    set_billing_provider(FakeStripe())
    assert await billing_usage.report_org_usage(db_session, org.id) == {}


@pytest.mark.asyncio
async def test_null_provider_report_usage_raises():
    with pytest.raises(BillingError):
        await NullProvider().report_usage(
            customer_id="c", meter_event="m", quantity=1, identifier="i"
        )


# --- scheduler -------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_enqueues_when_stripe_active(auth_client, db_session, monkeypatch):
    from datetime import date

    from app.core.config import settings
    from app.models.job import Job
    from app.services import scheduler

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test")  # active provider = stripe
    await _org(db_session)

    await scheduler.enqueue_daily(db_session, today=date(2026, 7, 21))
    kinds = list(await db_session.scalars(select(Job.kind)))
    assert "billing.report_usage" in kinds


@pytest.mark.asyncio
async def test_scheduler_skips_when_stripe_inactive(auth_client, db_session):
    from datetime import date

    from app.models.job import Job
    from app.services import scheduler

    await _org(db_session)  # has a customer id, but no Stripe key → provider not active
    await scheduler.enqueue_daily(db_session, today=date(2026, 7, 21))
    kinds = list(await db_session.scalars(select(Job.kind)))
    assert "billing.report_usage" not in kinds


# --- BILL-METER-001: frozen segments (Lago reference integration 2026-09-07) --


@pytest.mark.asyncio
async def test_lost_response_then_usage_growth_replays_same_fixed_segment(
    auth_client, db_session, monkeypatch
):
    """An ambiguous Stripe result may STALL billing; it must never OVER-report it.
    count 10 → provider accepts, response lost → count 15 → the retry sends the
    same 10 under the same identifier → only then 5 more under a new one."""
    _map_meter(monkeypatch)
    org = await _org(db_session)
    await _seed_usage(db_session, org.id, count=10)
    fake = AcceptThenLoseResponseStripe()
    set_billing_provider(fake)

    assert await billing_usage.report_org_usage(db_session, org.id) == {}
    counter = await db_session.scalar(select(UsageCounter))
    assert (counter.count, counter.reported, counter.reporting_target) == (10, 0, 10)
    first = fake.calls[0]
    assert first["quantity"] == 10 and first["identifier"].endswith(":upload:10")

    counter.count = 15  # usage keeps growing while the segment is in flight
    await db_session.commit()

    assert await billing_usage.report_org_usage(db_session, org.id) == {"upload": 10}
    second = fake.calls[1]
    assert second["quantity"] == 10  # NOT 15
    assert second["identifier"] == first["identifier"]  # provider-side dedupe key
    await db_session.refresh(counter)
    assert (counter.count, counter.reported, counter.reporting_target) == (15, 10, 10)

    assert await billing_usage.report_org_usage(db_session, org.id) == {"upload": 5}
    third = fake.calls[2]
    assert third["quantity"] == 5 and third["identifier"].endswith(":upload:15")
    await db_session.refresh(counter)
    assert (counter.reported, counter.reporting_target) == (15, 15)


@pytest.mark.asyncio
async def test_segment_is_committed_before_the_provider_call(auth_client, db_session, monkeypatch):
    """A worker that dies INSIDE the provider call must leave the frozen segment
    on disk, so the next run replays exactly it. Proven by ordering: the
    session's commit count at the instant the provider is called is one more
    than before the run, and the row read back after the (failed) run carries
    the frozen target. (A second-session read cannot prove this on SQLite's
    shared in-memory connection, which sees uncommitted flushes.)"""
    _map_meter(monkeypatch)
    org = await _org(db_session)
    await _seed_usage(db_session, org.id, count=7)
    commits = {"n": 0}
    real_commit = db_session.commit

    async def _counting_commit():
        commits["n"] += 1
        await real_commit()

    monkeypatch.setattr(db_session, "commit", _counting_commit)
    seen: dict[str, int] = {}

    class ObservingStripe(FakeStripe):
        async def report_usage(self, **kw):
            seen["commits_at_call"] = commits["n"]
            raise BillingError("worker died mid-call")

    set_billing_provider(ObservingStripe())
    assert await billing_usage.report_org_usage(db_session, org.id) == {}
    assert seen == {"commits_at_call": 1}  # the freeze was committed BEFORE the call
    await db_session.rollback()  # drop anything uncommitted, then read what is durable
    counter = await db_session.scalar(select(UsageCounter))
    assert (counter.reported, counter.reporting_target) == (0, 7)


@pytest.mark.asyncio
async def test_pre_migration_row_with_target_behind_reported_is_repaired(
    auth_client, db_session, monkeypatch
):
    _map_meter(monkeypatch)
    org = await _org(db_session)
    await _seed_usage(db_session, org.id, count=12, reported=5, reporting_target=0)
    fake = FakeStripe()
    set_billing_provider(fake)
    assert await billing_usage.report_org_usage(db_session, org.id) == {"upload": 7}
    counter = await db_session.scalar(select(UsageCounter))
    assert (counter.reported, counter.reporting_target) == (12, 12)
    assert fake.calls[0]["quantity"] == 7 and fake.calls[0]["identifier"].endswith(":upload:12")


@pytest.mark.asyncio
async def test_ack_then_local_commit_failure_replays_the_same_segment(
    auth_client, db_session, monkeypatch
):
    """Provider acknowledged 9 units, then OUR post-acknowledgement commit failed
    (worker killed, DB blip). Durable state still says reported < target, so
    the next run resends the SAME quantity under the SAME identifier — the
    provider's idempotency absorbs it — and only then advances."""
    _map_meter(monkeypatch)
    org = await _org(db_session)
    org_id = org.id  # the rollback below expires `org`; read the id before it
    await _seed_usage(db_session, org_id, count=9)
    fake = FakeStripe()
    set_billing_provider(fake)
    real_commit = db_session.commit
    state = {"commits": 0, "fail_on": 2}  # 1st commit = freeze, 2nd = post-ack advance

    async def _flaky_commit():
        state["commits"] += 1
        if state["commits"] == state["fail_on"]:
            await db_session.rollback()
            raise RuntimeError("connection lost before the advance committed")
        await real_commit()

    monkeypatch.setattr(db_session, "commit", _flaky_commit)
    with pytest.raises(RuntimeError):
        await billing_usage.report_org_usage(db_session, org_id)
    assert len(fake.calls) == 1 and fake.calls[0]["quantity"] == 9
    counter = await db_session.scalar(select(UsageCounter))
    assert (counter.reported, counter.reporting_target) == (0, 9)  # frozen, unacknowledged

    assert await billing_usage.report_org_usage(db_session, org_id) == {"upload": 9}
    assert len(fake.calls) == 2
    assert fake.calls[1]["identifier"] == fake.calls[0]["identifier"]
    assert fake.calls[1]["quantity"] == 9
    await db_session.refresh(counter)
    assert (counter.reported, counter.reporting_target) == (9, 9)


@pytest.mark.asyncio
async def test_post_ack_advance_is_compare_and_set(auth_client, db_session, monkeypatch):
    """Review A3: a run whose segment another run already advanced past must not
    regress `reported`. Simulated by a provider call during which the row moves
    on (a concurrent run acknowledged target 10 and then froze 15)."""
    from sqlalchemy import update

    _map_meter(monkeypatch)
    org = await _org(db_session)
    org_id = org.id
    await _seed_usage(db_session, org_id, count=10)

    class RowMovesOnStripe(FakeStripe):
        async def report_usage(self, **kw):
            await super().report_usage(**kw)
            # The "other run": acknowledged 10, usage grew, froze a new segment at 15.
            await db_session.execute(
                update(UsageCounter)
                .where(UsageCounter.org_id == org_id)
                .values(count=15, reported=10, reporting_target=15)
            )
            await db_session.commit()

    set_billing_provider(RowMovesOnStripe())
    assert await billing_usage.report_org_usage(db_session, org_id) == {}  # not ours to record
    counter = await db_session.scalar(select(UsageCounter))
    assert (counter.count, counter.reported, counter.reporting_target) == (15, 10, 15)  # untouched
