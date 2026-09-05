"""BE-003 (audit 2026-09-05) — a unique-key collision in `jobs.enqueue` unwinds
its own row only, never the caller's transaction.

`enqueue(commit=False)` is called from inside business operations that have
already staged their real work: `email_intake.process_attachment` has added the
InboundInvoice row and the document-registry row before it enqueues the
extract job; `webhooks.emit` runs after `pay_run` has recorded the payment. The
handler used to answer the IntegrityError with `db.rollback()` — the WHOLE
session — so the caller's flushed-but-uncommitted rows were expunged, `enqueue`
returned the winning job, and the route went on to commit an empty transaction
and answer 2xx. The fix is the SAVEPOINT `webhooks.emit` already uses.

Postgres-gated (`RLS_TEST_DATABASE_URL`), and deliberately so: the SQLite
harness has no pysqlite savepoint workaround, so a whole-session rollback there
does not actually undo a flushed INSERT and the defect is invisible — a
version of this test on SQLite passed with the defect seeded back in, which is
why it does not exist. On Postgres the seeded defect fails this test.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenant import reset_current_org, set_current_org
from app.models.job import Job
from app.models.organization import Organization
from app.services import jobs

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL,
    reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the savepoint test",
)


@pg_only
@pytest.mark.asyncio
async def test_be003_a_key_collision_leaves_the_callers_flushed_work_intact():
    engine = create_async_engine(PG_URL)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    org_id = str(uuid.uuid4())
    tok = set_current_org(org_id)
    try:
        async with sm() as s:
            s.add(Organization(id=org_id, name="Savepoint Co"))
            await s.commit()

        async with sm() as s:
            first = await jobs.enqueue(
                s, "recurring.generate", {}, org_id=org_id, idempotency_key="k-1"
            )
            # The pre-insert SELECT dedupes against LIVE rows only; the unique
            # index is unconditional. A finished job under the same key is
            # therefore exactly the shape that reaches the IntegrityError
            # handler (BE-004 records that mismatch) — and the only way to
            # exercise the handler without a real race.
            first.status = "succeeded"
            await s.commit()
            # The caller's real work: flushed in this transaction, not committed.
            staged = await jobs.enqueue(
                s, "dunning.run", {}, org_id=org_id, idempotency_key="staged", commit=False
            )
            staged_id = staged.id
            winner = await jobs.enqueue(
                s, "recurring.generate", {}, org_id=org_id, idempotency_key="k-1", commit=False
            )
            assert winner.id == first.id  # the dedupe still answers with the winner …
            await s.commit()

        async with sm() as s:
            survived = await s.scalar(select(Job.id).where(Job.id == staged_id))
        # … and the caller's staged row survived the collision.
        assert survived == staged_id, "the collision rolled back the caller's flushed work"
    finally:
        reset_current_org(tok)
        await engine.dispose()
