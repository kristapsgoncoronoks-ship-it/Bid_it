"""Worker parsing must use the admitted original, not changed content at its key."""

import base64

import pytest
from sqlalchemy import func, select

from app.core import storage
from app.models.capture_command import CaptureCommand
from app.models.email_intake import InboundInvoice
from app.models.extraction_field import ExtractionField
from app.models.extraction_run import ExtractionRun
from app.models.invoice import Invoice
from app.services import email_intake, filesec, jobs, parser

ORIGINAL = b"description,quantity,unit_price,tax_rate\nSynthetic trusted,1,100,21\n"
CHANGED = ORIGINAL.replace(b"trusted", b"changed").replace(b"100", b"999")


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["upload", "email"])
async def test_changed_source_never_publishes_a_draft_and_original_can_be_restored(
    auth_client, db_session, monkeypatch, channel
):
    command_key = "synthetic-original-integrity-command"
    if channel == "upload":
        response = await auth_client.post(
            "/api/v1/invoices/upload",
            files={"file": ("original.csv", ORIGINAL, "text/csv")},
            headers={"Idempotency-Key": command_key},
        )
        assert response.status_code == 202, response.text
        row = await db_session.get(ExtractionRun, response.json()["extraction_run_id"])
    else:
        monkeypatch.setattr(filesec.settings, "inbound_email_secret", "synthetic-hash-secret")
        assert (
            await auth_client.put("/api/v1/modules/email_intake", json={"enabled": True})
        ).status_code == 200
        address = (await auth_client.get("/api/v1/email/settings")).json()["address"]
        response = await auth_client.post(
            "/api/v1/email/inbound",
            headers={"X-Inbound-Secret": "synthetic-hash-secret"},
            json={
                "to": address,
                "message_id": "synthetic-original-integrity",
                "attachments": [
                    {
                        "filename": "original.csv",
                        "content_base64": base64.b64encode(ORIGINAL).decode(),
                    }
                ],
            },
        )
        assert response.status_code == 200 and response.json()["queued"] == 1, response.text
        row = await db_session.scalar(select(InboundInvoice))
    sha = row.source_sha256 if channel == "upload" else row.sha256
    assert sha == storage.sha256_hex(ORIGINAL)
    prefix = "uploads" if channel == "upload" else "email-attachments"
    key = storage.content_key(prefix, row.org_id, sha)
    store = storage.get_storage()
    store.put(key, CHANGED)  # only this test's isolated object; no admission bypass
    parsed = []
    original_parser = parser.parse_invoice_file

    def observe_parse(filename, content):
        parsed.append(content)
        return original_parser(filename, content)

    monkeypatch.setattr(parser, "parse_invoice_file", observe_parse)
    monkeypatch.setattr(email_intake, "parse_invoice_file", observe_parse)
    job = await jobs.run_once(db_session, "synthetic-integrity-worker")
    assert job is not None and job.status == "succeeded"
    await db_session.refresh(row)
    assert row.status == "failed", row.draft_json
    assert row.failure_code == "stored_file_changed"
    assert row.failure_seq == 1
    assert row.draft_json is None
    assert parsed == []
    assert store.get(key) == CHANGED
    assert await db_session.scalar(select(func.count()).select_from(Invoice)) == 0
    assert await db_session.scalar(select(func.count()).select_from(ExtractionField)) == 0
    assert (row.source_sha256 if channel == "upload" else row.sha256) == sha
    worklist = (await auth_client.get("/api/v1/invoices/captures/failures")).json()
    item = next(item for item in worklist["items"] if item["ref_id"] == row.id)
    assert item["code"] == "stored_file_changed"
    assert item["retry_helps"] is False
    assert item["document_retained"] is False  # no assurance about a verified original
    assert "restore" in item["remediation"]
    assert "administrator" in item["remediation"]
    if channel == "upload":
        lookup = await auth_client.get("/api/v1/invoices/upload-commands/" + command_key)
        assert lookup.json()["extraction_run_id"] == row.id
        assert await db_session.scalar(select(func.count()).select_from(CaptureCommand)) == 1
    outsider = await auth_client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Synthetic other workspace",
            "name": "Other owner",
            "email": "other-integrity@example.com",
            "password": "synthetic-other-integrity-password",
        },
    )
    assert outsider.status_code == 201, outsider.text
    other_headers = {"Authorization": "Bearer " + outsider.json()["token"]["access_token"]}
    other_worklist = await auth_client.get(
        "/api/v1/invoices/captures/failures", headers=other_headers
    )
    assert other_worklist.status_code == 200 and other_worklist.json()["items"] == []
    # An administrator restores exactly the original; neither a new trusted
    # fingerprint nor another upload command is manufactured by failure handling.
    store.put(key, ORIGINAL)
    if channel == "upload":
        retry = await auth_client.post(f"/api/v1/invoices/upload/{row.id}/retry")
        assert retry.status_code == 202, retry.text
        assert (await jobs.run_once(db_session, "synthetic-restored-worker")).status == "succeeded"
    else:
        # The existing worker service supports replay of failed email rows;
        # this does not claim an end-user email retry endpoint exists.
        await email_intake.extract_inbound(db_session, row.id)
    await db_session.refresh(row)
    assert row.status == ("parsed" if channel == "upload" else "pending")
    assert row.failure_code is None
    assert parsed == [ORIGINAL]
    assert (row.source_sha256 if channel == "upload" else row.sha256) == sha
