"""Capture admission must refuse bounded input/storage faults before queuing work."""

import errno

import pytest
from sqlalchemy import func, select
from starlette.datastructures import UploadFile

from app.core import storage
from app.models.document import Document
from app.models.extraction_run import ExtractionRun
from app.models.job import Job
from app.models.usage import UsageCounter
from app.services import filesec

CSV = b"description,quantity,unit_price,tax_rate\nSynthetic service,1,100,21\n"


async def counts(db):
    return [
        await db.scalar(select(func.count()).select_from(m))
        for m in (Document, ExtractionRun, Job, UsageCounter)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
async def test_capture_reads_only_cap_plus_one_and_does_not_scan_or_queue(
    auth_client, db_session, monkeypatch, batch
):
    monkeypatch.setattr(filesec.settings, "max_upload_mb", 1)
    original_read = UploadFile.read
    requested = []
    consumed = 0

    async def guarded_read(self, size=-1):
        nonlocal consumed
        assert 0 < size <= 65536, "Capture requested an unbounded file read"
        requested.append(size)
        data = await original_read(self, size)
        consumed += len(data)
        return data

    async def forbidden_scan(*args, **kwargs):
        pytest.fail("Oversized input reached the scanner")

    monkeypatch.setattr(UploadFile, "read", guarded_read)
    monkeypatch.setattr(filesec, "check_async", forbidden_scan)
    part = ("large.csv", b"x" * (filesec.max_bytes() + 100), "text/csv")
    path = "/api/v1/invoices/upload" + ("/batch" if batch else "")
    response = await auth_client.post(path, files={"files" if batch else "file": part})
    if batch:
        assert response.status_code == 202
        assert response.json()["outcomes"][0]["code"] == "file_too_large"
        assert response.json()["accepted"] == 0
    else:
        assert response.status_code == 413
    assert requested and consumed == filesec.max_bytes() + 1
    assert await counts(db_session) == [0, 0, 0, 0]


@pytest.mark.asyncio
async def test_capture_storage_fault_is_safe_to_retry_without_phantom_work(
    auth_client, db_session, monkeypatch
):
    store = storage.get_storage()
    original_put = store.put

    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, "private storage location must not reach the client")

    monkeypatch.setattr(store, "put", fail)
    response = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
    )
    assert response.status_code == 503
    assert "private storage" not in response.text
    assert "Retry" in response.json()["detail"]
    assert await counts(db_session) == [0, 0, 0, 0]
    monkeypatch.setattr(store, "put", original_put)
    retry = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
    )
    assert retry.status_code == 202
    assert await counts(db_session) == [1, 1, 1, 1]


@pytest.mark.asyncio
async def test_batch_storage_fault_keeps_other_files_and_reports_only_failed_file(
    auth_client, db_session, monkeypatch
):
    store = storage.get_storage()
    original_put = store.put

    def selective_failure(key, data, content_type=None):
        if data == CSV:
            raise storage.StorageError("private backend diagnostic")
        return original_put(key, data, content_type)

    monkeypatch.setattr(store, "put", selective_failure)
    good = CSV.replace(b"Synthetic service", b"Synthetic other")
    response = await auth_client.post(
        "/api/v1/invoices/upload/batch",
        files=[
            ("files", ("good.csv", good, "text/csv")),
            ("files", ("bad.csv", CSV, "text/csv")),
            ("files", ("last.csv", good + b"\n", "text/csv")),
        ],
    )
    assert response.status_code == 202
    result = response.json()
    assert (result["accepted"], result["rejected"]) == (2, 1)
    assert [x["accepted"] for x in result["outcomes"]] == [True, False, True]
    assert result["outcomes"][1]["code"] == "storage_unavailable"
    assert "private backend" not in response.text
    assert await counts(db_session) == [2, 2, 2, 1]
    assert await db_session.scalar(select(UsageCounter.count)) == 2
