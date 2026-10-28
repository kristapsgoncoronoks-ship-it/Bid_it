"""Region admission must coexist with capture writers and their tenant FKs.

An Organization FOR UPDATE admission lock conflicts with the KEY SHARE lock
PostgreSQL takes when an already-running capture writer inserts a child row.
Capture-first writers can then deadlock an admission waiting for that capture.
The admission mutex only needs FOR NO KEY UPDATE: competing admissions still
serialize, while child-FK validation can complete and release the capture.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import uuid

import pytest
from sqlalchemy import select

from app.core.tenant import reset_current_org, set_current_org
from app.models.capture_region import CaptureRegionRead
from app.models.job import Job
from app.services import capture_region, extraction, region_ocr
from tests.test_capture_region_pg import (
    BASE,
    KIND,
    _identity,
    _post_read,
    _read_body,
    _ready,
    _seed_capture,
    _view,
)
from tests.test_capture_region_pg import (
    region_pg as region_pg,
)

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["append", "manual_review", "worker_publication"])
async def test_new_read_admission_does_not_deadlock_capture_writer(region_pg, monkeypatch, writer):
    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity, reviewed=True)
    first_read = await _post_read(
        client,
        identity,
        capture,
        _read_body(capture, target="table" if writer == "append" else "field"),
    )
    if writer != "worker_publication":
        await _ready(factory, capture, first_read["id"], table=writer == "append")
    initial = await _view(client, identity, capture, first_read["id"])
    marker = contextvars.ContextVar("region_lock_test_request", default="")
    capture_locked, admission_locked, release_writer = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    original_get = extraction.get_capture
    writer_locks = 0

    async def gated_get(db, org_id, run_id, *, for_update=False):
        nonlocal writer_locks
        if marker.get() == "admission" and for_update:
            # create() has already acquired its organization admission mutex.
            admission_locked.set()
        row = await original_get(db, org_id, run_id, for_update=for_update)
        if marker.get() == "writer" and for_update:
            writer_locks += 1
            # The worker's first lock commits its running claim before native
            # I/O; the second lock publishes the result and inserts its audit.
            boundary = 2 if writer == "worker_publication" else 1
            if writer_locks == boundary:
                capture_locked.set()
                await asyncio.wait_for(release_writer.wait(), timeout=10)
        return row

    monkeypatch.setattr(extraction, "get_capture", gated_get)
    monkeypatch.setattr(
        region_ocr,
        "recognize",
        lambda content, request: {
            "text": "NEW-002",
            "engine": "tesseract",
            "language": "eng",
            "installed_languages": ["eng"],
            "page": 1,
            "rotation": 0,
            "source_width": 1200,
            "source_height": 900,
            "rotated_width": 1200,
            "rotated_height": 900,
            "crop_width": 960,
            "crop_height": 540,
            "render_dpi": None,
        },
    )

    async def write_capture():
        token = marker.set("writer")
        try:
            if writer == "append":
                return await client.post(
                    f"{BASE}/{capture['id']}/region-reads/{first_read['id']}/apply",
                    headers=identity["headers"],
                    json={
                        "command_key": str(uuid.uuid4()),
                        "expected_review_fingerprint": initial["review_fingerprint"],
                        "fields": [],
                        "overwrite_reviewed": False,
                        "rows": [
                            {
                                "candidate_index": 0,
                                "target_line_index": None,
                                "fields": ["description", "quantity", "unit_price", "amount"],
                            }
                        ],
                    },
                )
            if writer == "manual_review":
                return await client.post(
                    f"{BASE}/{capture['id']}/review",
                    headers=identity["headers"],
                    json={
                        "fields": [{"field": "invoice_number", "reviewed_value": "MANUAL-LOCK-001"}]
                    },
                )
            org_token = set_current_org(capture["org_id"])
            try:
                async with factory() as db:
                    job = await db.scalar(
                        select(Job).where(
                            Job.org_id == capture["org_id"],
                            Job.kind == KIND,
                            Job.idempotency_key == f"capture-region:{first_read['id']}",
                        )
                    )
                    assert job is not None
                    return await capture_region.run_region_read(
                        db, {"region_read_id": first_read["id"]}, job
                    )
            finally:
                reset_current_org(org_token)
        finally:
            marker.reset(token)

    async def admit_read():
        token = marker.set("admission")
        try:
            return await client.post(
                f"{BASE}/{capture['id']}/region-reads",
                headers=identity["headers"],
                json=_read_body(capture),
            )
        finally:
            marker.reset(token)

    writing = asyncio.create_task(write_capture())
    admission = None
    try:
        await asyncio.wait_for(capture_locked.wait(), timeout=10)
        admission = asyncio.create_task(admit_read())
        await asyncio.wait_for(admission_locked.wait(), timeout=10)
        release_writer.set()
        results = await asyncio.wait_for(
            asyncio.gather(writing, admission, return_exceptions=True), timeout=20
        )
    finally:
        release_writer.set()
        tasks = [writing] + ([admission] if admission is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert not any(isinstance(result, BaseException) for result in results), [
        str(r) for r in results
    ]
    written, accepted = results
    if writer == "worker_publication":
        assert written == {"status": "ready"}
    else:
        assert written.status_code == 200, written.text
    assert accepted.status_code == 202, accepted.text
    org_token = set_current_org(capture["org_id"])
    try:
        async with factory() as db:
            prior = await db.get(CaptureRegionRead, first_read["id"])
            assert prior is not None
            assert prior.status == ("applied" if writer == "append" else "ready")
    finally:
        reset_current_org(org_token)
