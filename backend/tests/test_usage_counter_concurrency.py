"""The metered-usage counter under interleaving writers (P2-1 of the bug scan).

`access.record_usage` used to be a read-modify-write in Python: read N, write
N+1, commit. Two uploads landing at once both read the same N and one increment
was silently lost — and this counter IS the upload quota, the paid boundary
between plan tiers, so the loss fell precisely on the tenants busy enough to be
paying. The fix makes the increment a single database upsert whose conflict arm
is `count = count + n`, evaluated server-side, so there is no window between the
read and the write for another writer to stand in.

Two tests, following the harness's established split (`test_numbering_
concurrency.py`): the upsert's OBSERVABLE behaviour (create, then increment,
across separate sessions) runs on the default SQLite suite; the genuinely
CONCURRENT proof needs real parallel connections, which the single-connection
SQLite harness cannot provide, so it is Postgres-gated via
`RLS_TEST_DATABASE_URL` and runs in the Postgres CI job.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenant import reset_current_org, set_current_org
from app.models.organization import Organization
from app.models.usage import UsageCounter
from app.services import access

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL,
    reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the concurrency test",
)

_N = 16  # concurrent increments on the same (org, period, metric) row


@pytest.mark.asyncio
async def test_record_usage_creates_then_increments_one_row(auth_client, db_session):
    """The upsert's two arms, in order: first call creates the row at n, second
    call takes the conflict arm and adds to it — one row, correct total."""
    org_id = await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))

    await access.record_usage(db_session, org_id, "upload")
    await access.record_usage(db_session, org_id, "upload", n=2)

    rows = list(
        await db_session.scalars(
            select(UsageCounter).where(
                UsageCounter.org_id == org_id, UsageCounter.metric == "upload"
            )
        )
    )
    assert len(rows) == 1, "the conflict arm must land on the existing row, not add a second"
    assert rows[0].count == 3
    # `reported` (the Stripe watermark) must survive the upsert untouched —
    # clobbering it would re-report already-billed usage.
    assert rows[0].reported == 0


@pg_only
@pytest.mark.asyncio
async def test_concurrent_increments_are_all_counted():
    """Fire N truly-concurrent increments, each on its own session/connection.
    The old read-modify-write lost increments here (both workers read N, both
    wrote N+1); the server-side conflict arm cannot."""
    engine = create_async_engine(PG_URL, pool_size=_N + 4, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    org_id = str(uuid.uuid4())
    try:
        async with sm() as s:
            s.add(Organization(id=org_id, name="Metered Co"))
            await s.commit()

        async def bump() -> None:
            tok = set_current_org(org_id)
            try:
                async with sm() as s:
                    await access.record_usage(s, org_id, "upload")
            finally:
                reset_current_org(tok)

        await asyncio.gather(*[bump() for _ in range(_N)])

        tok = set_current_org(org_id)
        try:
            async with sm() as s:
                count = await s.scalar(
                    select(UsageCounter.count).where(
                        UsageCounter.org_id == org_id, UsageCounter.metric == "upload"
                    )
                )
        finally:
            reset_current_org(tok)

        assert count == _N, f"lost {_N - (count or 0)} increment(s) under concurrency"
    finally:
        await engine.dispose()
