"""BILL-QA-001 (Lago reference integration 2026-09-07) — billing reliability
SCENARIOS: distributed-failure shapes that narrow unit tests miss. Each asserts
effects, identifiers, quantities and durable state — never call counts alone.

  1. webhook accepted → worker dies mid-run → lease reclaimed → re-run applies ONCE
  2. webhook accepted → redelivered twice while the job is still queued → one job, one apply
  3. usage segment accepted by the provider, response lost, usage grows → replay of the
     same segment (see `test_billing_usage.py::test_lost_response_then_usage_growth_…`)
  4. an older event retried AFTER a newer event for the same subscription applied is
     superseded — a cancelled tenant does not return to a paid plan (review R-1)
  5. provider acknowledged, then OUR commit failed → the same segment replays under the
     same identifier (`test_billing_usage.py::test_ack_then_local_commit_failure_…`)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.billing_event import ProcessedStripeEvent
from app.models.job import Job
from app.models.organization import Organization
from app.services import job_handlers, jobs
from app.services.billing_provider import SubscriptionEvent, set_billing_provider
from tests.test_billing_stripe import FakeProvider

HEADERS = {"stripe-signature": "t=1,v1=abc"}


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_billing_provider(None)


async def _bind_customer(db) -> Organization:
    org = await db.scalar(select(Organization))
    org.stripe_customer_id = "cus_fake123"
    await db.commit()
    return org


@pytest.mark.asyncio
async def test_worker_death_mid_apply_is_reclaimed_and_applied_exactly_once(
    auth_client, db_session
):
    org = await _bind_customer(db_session)
    ev = SubscriptionEvent(
        "evt_crash", "checkout.session.completed", "cus_fake123", "sub_c", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))
    r = await auth_client.post("/api/v1/billing/webhook", content=b"{}", headers=HEADERS)
    assert r.status_code == 200 and r.json()["created"] is True

    # Worker A claims the job and dies before applying (no _complete/_fail).
    claimed = await jobs.claim(
        db_session, "worker-a", kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,)
    )
    assert claimed is not None and claimed.status == "running"
    claimed.locked_at = datetime.now(UTC) - timedelta(seconds=jobs.STALE_LEASE_SECONDS + 1)
    await db_session.commit()
    await db_session.refresh(org)
    assert org.plan != "pro"  # nothing applied yet

    # The sweep returns it to the queue (with a backoff); worker B runs it.
    assert await jobs.reclaim_stale(db_session) == 1
    ran = await jobs.run_once(
        db_session,
        "worker-b",
        kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,),
        now=datetime.now(UTC) + timedelta(seconds=jobs._backoff(1) + 1),
    )
    assert ran is not None and ran.status == "succeeded" and ran.attempts == 2
    await db_session.refresh(org)
    assert org.plan == "pro"
    assert org.stripe_subscription_id == "sub_c"
    ledger_rows = await db_session.scalar(
        select(func.count())
        .select_from(ProcessedStripeEvent)
        .where(ProcessedStripeEvent.event_id == "evt_crash")
    )
    assert ledger_rows == 1  # applied once, recorded once


@pytest.mark.asyncio
async def test_redelivery_while_queued_yields_one_job_and_one_application(auth_client, db_session):
    org = await _bind_customer(db_session)
    ev = SubscriptionEvent(
        "evt_dup", "customer.subscription.updated", "cus_fake123", "sub_d", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))
    outcomes = []
    for _ in range(3):
        r = await auth_client.post("/api/v1/billing/webhook", content=b"{}", headers=HEADERS)
        assert r.status_code == 200
        outcomes.append(r.json()["created"])
    assert outcomes == [True, False, False]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.idempotency_key == "evt_dup")
        )
        == 1
    )
    ran = await jobs.run_once(db_session, "w", kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,))
    assert ran is not None and ran.status == "succeeded"
    assert (
        await jobs.run_once(db_session, "w", kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,))
        is None
    )  # nothing else queued
    await db_session.refresh(org)
    assert org.plan == "pro"


@pytest.mark.asyncio
async def test_a_stale_retry_does_not_undo_a_newer_event_for_the_same_subscription(
    auth_client, db_session, monkeypatch
):
    """`updated{active,pro}` fails transiently; `deleted{canceled}` arrives and applies
    (tenant → free plan); the retry of the older event must apply NOTHING."""
    from app.services import billing as billing_svc
    from app.services import plans

    org = await _bind_customer(db_session)
    older = SubscriptionEvent(
        "evt_older", "customer.subscription.updated", "cus_fake123", "sub_s", "pro", "active"
    )
    newer = SubscriptionEvent(
        "evt_newer", "customer.subscription.deleted", "cus_fake123", "sub_s", None, "canceled"
    )
    set_billing_provider(FakeProvider(older))
    assert (
        await auth_client.post("/api/v1/billing/webhook", content=b"{}", headers=HEADERS)
    ).status_code == 200

    real_apply = billing_svc.apply_subscription_event

    async def transient(db, event):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(billing_svc, "apply_subscription_event", transient)
    failed = await jobs.run_once(db_session, "w", kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,))
    assert failed is not None and failed.status == "queued"
    monkeypatch.setattr(billing_svc, "apply_subscription_event", real_apply)

    set_billing_provider(FakeProvider(newer))
    assert (
        await auth_client.post("/api/v1/billing/webhook", content=b"{}", headers=HEADERS)
    ).status_code == 200
    applied_newer = await jobs.run_once(
        db_session, "w", kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,)
    )
    assert applied_newer is not None and applied_newer.status == "succeeded"
    await db_session.refresh(org)
    assert org.status == "canceled" and org.plan == plans.DEFAULT_PLAN

    # The older event's retry comes due.
    retried = await jobs.run_once(
        db_session,
        "w",
        kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,),
        now=datetime.now(UTC) + timedelta(seconds=jobs._backoff(1) + 1),
    )
    assert retried is not None and retried.status == "succeeded"
    assert '"superseded"' in (retried.result_json or "")
    await db_session.refresh(org)
    assert org.status == "canceled" and org.plan == plans.DEFAULT_PLAN  # NOT back on pro


@pytest.mark.asyncio
async def test_a_signed_event_without_an_id_queues_nothing(auth_client, db_session):
    """Only the signing secret can produce this shape; without an id there is no
    idempotency key, so it must not become a job per delivery (review S-4)."""
    await _bind_customer(db_session)
    ev = SubscriptionEvent(
        "", "customer.subscription.updated", "cus_fake123", "sub_n", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))
    for _ in range(2):
        r = await auth_client.post("/api/v1/billing/webhook", content=b"{}", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == {"received": True, "queued": False, "created": False}
    assert await db_session.scalar(select(func.count()).select_from(Job)) == 0
