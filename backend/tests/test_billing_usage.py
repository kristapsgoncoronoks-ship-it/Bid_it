"""Metered-usage reporting to Stripe (Phase 3.9 continuation, ADR-0013):
incremental idempotent delta reporting, provider/meter gating, scheduler."""

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


async def _seed_usage(db, org_id, count, reported=0):
    db.add(
        UsageCounter(
            org_id=org_id, period=_period(), metric="upload", count=count, reported=reported
        )
    )
    await db.commit()


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
