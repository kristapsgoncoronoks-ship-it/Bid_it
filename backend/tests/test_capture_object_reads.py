"""Stored capture input must be bounded before parsing, including worker recovery."""

import base64
import io
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core import storage
from app.models.email_intake import InboundInvoice
from app.models.extraction_run import ExtractionRun
from app.services import filesec, jobs

CSV = b"description,quantity,unit_price,tax_rate\nSynthetic read boundary,1,100,21\n"


@pytest.mark.parametrize("failure", [False, True])
def test_s3_body_is_closed_after_success_or_read_error(failure):
    class Body(io.BytesIO):
        def read(self, *args):
            if failure:
                raise OSError("synthetic private backend error")
            return super().read(*args)

    body = Body(CSV)
    store = storage.S3Storage.__new__(storage.S3Storage)
    store._prefix = ""
    store._bucket = "synthetic"
    store._client = SimpleNamespace(get_object=lambda **kw: {"Body": body})
    if failure:
        with pytest.raises(storage.StorageError):
            store.get("source")
    else:
        assert store.get("source") == CSV
    assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["upload", "email"])
async def test_oversized_stored_capture_is_visible_and_next_job_proceeds(
    auth_client, db_session, monkeypatch, channel
):
    if channel == "upload":
        accepted = await auth_client.post(
            "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
        )
        assert accepted.status_code == 202, accepted.text
        row = await db_session.get(ExtractionRun, accepted.json()["extraction_run_id"])
    else:
        monkeypatch.setattr(filesec.settings, "inbound_email_secret", "synthetic-read-secret")
        assert (
            await auth_client.put("/api/v1/modules/email_intake", json={"enabled": True})
        ).status_code == 200
        address = (await auth_client.get("/api/v1/email/settings")).json()["address"]
        accepted = await auth_client.post(
            "/api/v1/email/inbound",
            headers={"X-Inbound-Secret": "synthetic-read-secret"},
            json={
                "to": address,
                "message_id": "synthetic-stored-read",
                "attachments": [
                    {"filename": "source.csv", "content_base64": base64.b64encode(CSV).decode()}
                ],
            },
        )
        assert accepted.status_code == 200, accepted.text
        row = await db_session.scalar(select(InboundInvoice))
    assert row is not None
    # A changed processing cap / oversized restored object must be caught at
    # storage, before allocating/parsing the whole original. No admission cap
    # is bypassed to set up this pre-existing reference.
    with monkeypatch.context() as patch:
        patch.setattr(filesec, "max_bytes", lambda purpose=None: 32)
        job = await jobs.run_once(db_session, "synthetic-bounded-reader")
        assert job is not None and job.status == "succeeded"
    await db_session.refresh(row)
    assert row.status == "failed"
    assert row.failure_code == "stored_file_too_large"
    assert row.failure_seq == 1
    worklist = await auth_client.get("/api/v1/invoices/captures/failures")
    item = next(i for i in worklist.json()["items"] if i["ref_id"] == row.id)
    assert item["document_retained"] is True
    assert item["retry_helps"] is False
    assert "administrator" in item["remediation"]
    prefix = storage.content_key(
        "uploads" if channel == "upload" else "email-attachments",
        row.org_id,
        row.source_sha256 if channel == "upload" else row.sha256,
    )
    assert storage.get_storage().get(prefix) == CSV
    next_upload = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("next.csv", CSV + b"\n", "text/csv")}
    )
    assert next_upload.status_code == 202
    next_job = await jobs.run_once(db_session, "synthetic-bounded-reader")
    assert next_job is not None and next_job.status == "succeeded"
    follow = await db_session.get(ExtractionRun, next_upload.json()["extraction_run_id"])
    assert follow.status == "parsed"
