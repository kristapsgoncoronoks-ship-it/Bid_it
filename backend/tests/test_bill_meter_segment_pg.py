"""BILL-METER-001 on Postgres — what SQLite's shared in-memory connection cannot
prove: (1) the frozen segment is COMMITTED before the provider call (a second
connection reads it under READ COMMITTED), and (2) the counter row's `FOR UPDATE`
lock is RELEASED before the call on both the freeze and the replay path (a second
connection's `FOR UPDATE NOWAIT` succeeds) — otherwise this tenant's upload
counter (`access.record_usage`, same row) would stall for the length of a Stripe
call (review finding P1).

Postgres-gated (`RLS_TEST_DATABASE_URL`).
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.tenant import reset_current_org, set_current_org
from app.models.organization import Organization
from app.models.usage import UsageCounter
from app.services import billing_usage
from app.services.access import _period
from app.services.billing_provider import BillingError, set_billing_provider

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the meter test"
)


@pg_only
@pytest.mark.asyncio
async def test_bill_meter_segment_is_durable_and_unlocked_before_the_provider_call(
    monkeypatch,
):
    monkeypatch.setattr(settings, "stripe_meter_upload", "uploads_meter")
    engine = create_async_engine(PG_URL)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    org_id = str(uuid.uuid4())
    tok = set_current_org(org_id)
    observed: list[tuple[int, int]] = []

    class Observer:
        kind = "subscription"
        name = "stripe"
        enabled = True

        def __init__(self, fail: bool):
            self.fail = fail
            self.calls: list[dict] = []

        async def report_usage(self, **kw):
            self.calls.append(kw)
            # A second connection: what is durable, and is the row unlocked?
            async with sm() as other:
                row = (
                    await other.execute(
                        text(
                            "SELECT reported, reporting_target FROM usage_counters "
                            "WHERE org_id = :org FOR UPDATE NOWAIT"
                        ),
                        {"org": org_id},
                    )
                ).one()
                await other.rollback()
            observed.append((row[0], row[1]))
            if self.fail:
                raise BillingError("lost response")

    try:
        async with sm() as s:
            s.add(Organization(id=org_id, name="Fuel Meter Co", stripe_customer_id="cus_pg_meter"))
            await s.commit()
            s.add(UsageCounter(org_id=org_id, period=_period(), metric="upload", count=7))
            await s.commit()

        # Freeze path: the provider call sees the committed, unlocked segment.
        first = Observer(fail=True)
        set_billing_provider(first)
        async with sm() as s:
            assert await billing_usage.report_org_usage(s, org_id) == {}
        assert observed == [(0, 7)]
        assert first.calls[0]["quantity"] == 7

        # Replay path (reported < reporting_target): still unlocked during the call.
        second = Observer(fail=False)
        set_billing_provider(second)
        async with sm() as s:
            assert await billing_usage.report_org_usage(s, org_id) == {"upload": 7}
        assert observed[-1] == (0, 7)
        assert second.calls[0]["identifier"] == first.calls[0]["identifier"]
        async with sm() as s:
            row = await s.scalar(select(UsageCounter).where(UsageCounter.org_id == org_id))
            assert (row.reported, row.reporting_target) == (7, 7)
    finally:
        set_billing_provider(None)
        # Clean up INSIDE the tenant scope (the counter row is RLS-protected), then reset.
        async with sm() as s:
            for row in list(
                await s.scalars(select(UsageCounter).where(UsageCounter.org_id == org_id))
            ):
                await s.delete(row)
            await s.commit()
        reset_current_org(tok)
        async with sm() as s:
            org = await s.get(Organization, org_id)
            if org is not None:
                await s.delete(org)
            await s.commit()
        await engine.dispose()
