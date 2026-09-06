"""ARCH-013 / PERF-011 (audit 2026-09-05, P2 batch 4) — one worker runs the
daily sweep at a time. Postgres only: the transaction-scoped advisory lock has
no SQLite equivalent (a single process needs none), so this runs where
`RLS_TEST_DATABASE_URL` points at the migrated Postgres the CI `postgres` job
provides, and skips on the SQLite suite.

Two connections: while the first holds the lock inside an open transaction,
the second's sweep returns 0 without reading a single tenant; once the first
commits, the second takes the lock. Nothing is enqueued and nothing persists.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services import scheduler

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a migrated Postgres URL) to run"
)


@pg_only
@pytest.mark.asyncio
async def test_a_second_worker_skips_the_sweep_while_the_first_holds_the_lock():
    engine = create_async_engine(PG_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.connect() as holder:
            await holder.begin()
            held = await holder.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                {"k": scheduler.ADVISORY_LOCK_KEY},
            )
            assert held is True

            async with sessions() as other:
                assert await scheduler._take_daily_lock(other) is False
                assert await scheduler.enqueue_daily(other) == 0
                await other.rollback()

            await holder.rollback()  # releases the xact lock

        async with sessions() as other:
            assert await scheduler._take_daily_lock(other) is True
            await other.rollback()
    finally:
        await engine.dispose()
