"""Concurrent writers must share metadata without losing their caller's work."""

import asyncio
import os

import pytest
from sqlalchemy import func, select

from app.models.document import Document
from app.models.job import Job
from app.services import document_registry
from tests.test_capture_admission_pg import quota_workspace as quota_workspace

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_first", [False, True])
async def test_concurrent_same_object_preserves_outer_transactions(
    quota_workspace, monkeypatch, rollback_first
):
    _, factory, org_id, _ = quota_workspace
    both_read, first_stored, second_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
    reads = 0
    sessions = [factory(), factory()]
    for index, db in enumerate(sessions):
        original = db.scalar

        def gated_scalar(original=original, index=index):
            first = True

            async def run(statement, *args, **kwargs):
                nonlocal first, reads
                result = await original(statement, *args, **kwargs)
                if first:
                    first = False
                    assert result is None
                    reads += 1
                    if reads == 2:
                        both_read.set()
                    await asyncio.wait_for(both_read.wait(), 10)
                    if index == 1:
                        await asyncio.wait_for(first_stored.wait(), 10)
                        second_ready.set()
                return result

            return run

        monkeypatch.setattr(db, "scalar", gated_scalar())

    async def writer(index):
        async with sessions[index] as db:
            # Unrelated pending caller work must survive the losing registry
            # insert. The registry is not allowed to roll back/commit this job.
            db.add(
                Job(
                    org_id=org_id,
                    kind="synthetic.registry",
                    payload_json="{}",
                    idempotency_key=str(index),
                )
            )
            doc = await document_registry.register(
                db,
                org_id,
                sha256="a" * 64,
                size=17,
                kind="email-attachments",
                filename=f"synthetic-{index}.csv",
                uploaded_by=f"synthetic-{index}@example.com",
            )
            if index == 0:
                first_stored.set()
                async with factory() as observer:
                    assert (
                        await document_registry.find(
                            observer, org_id, sha256="a" * 64, kind="email-attachments"
                        )
                        is None
                    )
                await asyncio.wait_for(second_ready.wait(), 10)
                if rollback_first:
                    await db.rollback()
                    return None
            await db.commit()
            return doc.id

    ids = await asyncio.wait_for(asyncio.gather(writer(0), writer(1)), 20)
    async with factory() as db:
        docs = list(await db.scalars(select(Document).where(Document.org_id == org_id)))
        assert len(docs) == 1
        assert docs[0].filename == "synthetic-1.csv"
        assert docs[0].uploaded_by == f"synthetic-{1 if rollback_first else 0}@example.com"
        assert await db.scalar(
            select(func.count()).select_from(Job).where(Job.org_id == org_id)
        ) == (1 if rollback_first else 2)
        assert ids[1] == docs[0].id
        if not rollback_first:
            assert ids[0] == ids[1]
