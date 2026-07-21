"""Async direct-upload capture (Stage B): the upload endpoint accepts the file
and QUEUES the parse on the worker tier (no OCR in the web request). The client
polls for the draft; a bad parse surfaces as a failed status, not a 5xx."""

import io

import pytest

from app.services import jobs

_CSV = (
    "description,category,quantity,unit_price,tax_rate,vendor,invoice_number,issue_date\n"
    "Fuel,fuel,10,1.50,21,Shell,INV-A,2026-02-01\n"
)


@pytest.mark.asyncio
async def test_upload_is_queued_then_parsed(auth_client, db_session):
    files = {"file": ("a.csv", io.BytesIO(_CSV.encode()), "text/csv")}
    r = await auth_client.post("/api/v1/invoices/upload", files=files)
    assert r.status_code == 202, r.text
    run_id = r.json()["extraction_run_id"]
    assert r.json()["status"] == "queued"

    # Before the worker runs, the draft is not ready yet (no OCR happened in-request).
    pending = (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()
    assert pending["status"] == "queued"
    assert pending["draft"] is None

    # Run the worker → the parse happens off the web tier and the draft appears.
    job = await jobs.run_once(db_session, "w")
    assert job is not None and job.kind == "upload.extract"
    assert job.status == "succeeded"

    done = (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()
    assert done["status"] == "parsed"
    assert done["method"] == "csv"
    assert done["draft"]["draft"]["vendor_name"] == "Shell"
    assert done["draft"]["extraction_run_id"] == run_id


@pytest.mark.asyncio
async def test_upload_status_unknown_run_404(auth_client):
    r = await auth_client.get("/api/v1/invoices/upload/does-not-exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_extract_runs_on_the_extract_lane(auth_client, db_session):
    """The parse job is claimable by the OCR lane (kinds=upload.extract) — the
    isolation that keeps heavy OCR off both the web tier and the light job pool."""
    files = {"file": ("a.csv", io.BytesIO(_CSV.encode()), "text/csv")}
    r = await auth_client.post("/api/v1/invoices/upload", files=files)
    run_id = r.json()["extraction_run_id"]

    # The general lane (excludes upload.extract) must NOT pick it up…
    assert await jobs.run_once(db_session, "gen", exclude=("upload.extract",)) is None
    # …but the extract lane does.
    job = await jobs.run_once(db_session, "extract", kinds=("upload.extract",))
    assert job is not None and job.kind == "upload.extract"

    done = (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()
    assert done["status"] == "parsed"
