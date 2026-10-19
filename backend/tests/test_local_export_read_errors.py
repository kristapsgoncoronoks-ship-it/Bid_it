"""Real local I/O failures must not become missing-file export success."""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core import storage
from app.core.errors import ConflictError
from app.models.archive_export import ArchiveExport
from app.models.archived_invoice import ArchivedInvoice
from app.models.audit import AuditEvent
from app.models.document import Document
from app.models.email_message import EmailMessage
from app.models.email_token import EmailToken
from app.models.workspace_export import WorkspaceExport
from app.services import audit, documents, jobs
from app.services.transport import claim_pack

PAYLOAD = b"%PDF-1.4 synthetic original retained for export recovery"


def _model(kind):
    return WorkspaceExport if kind == "workspace" else ArchiveExport


async def _seed(auth_client, db_session, tmp_path):
    me = await auth_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    org_id = me.json()["organization"]["id"]
    local = storage.LocalStorage(str(tmp_path / "objects"))
    storage.set_storage(local)
    sha, _ = await documents.store(
        documents.UPLOADS,
        org_id,
        PAYLOAD,
        "application/pdf",
        db=db_session,
        filename="retained-original.pdf",
    )
    now = datetime.now(UTC)
    db_session.add(
        ArchivedInvoice(
            org_id=org_id,
            original_invoice_id=str(uuid4()),
            invoice_number="ARC-READ-RECOVERY",
            vendor_name="Synthetic Parts",
            source_sha256=sha,
            source_filename="retained-original.pdf",
            archived_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=700),
        )
    )
    await db_session.commit()
    return org_id, sha, local


async def _request(auth_client, kind):
    asked = await auth_client.post(f"/api/v1/{kind}/export")
    assert asked.status_code == 202, asked.text
    return asked.json()["id"]


async def _run(db_session, kind):
    job = await jobs.run_once(db_session, "local-read-recovery", kinds=(f"{kind}.export",))
    assert job is not None
    return job


def _assert_zip(data, kind, *, missing):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if kind == "workspace":
            entries = json.loads(zf.read("documents/index.json"))
            assert len(entries) == 1
            assert entries[0]["status"] == ("missing" if missing else "present")
            assert json.loads(zf.read("manifest.json"))["missing_documents"] == int(missing)
            paths = [entries[0]["path"]] if not missing else []
        else:
            manifest = zf.read("manifest.csv").decode()
            assert "ARC-READ-RECOVERY" in manifest
            assert ("missing" if missing else "included") in manifest
            paths = [name for name in zf.namelist() if name.startswith("documents/")]
        assert len(paths) == (0 if missing else 1)
        if paths:
            assert zf.read(paths[0]) == PAYLOAD


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["workspace", "archive"])
async def test_local_read_failure_does_not_publish_partial_export_and_worker_retry_recovers(
    auth_client, db_session, tmp_path, monkeypatch, kind
):
    org_id, sha, local = await _seed(auth_client, db_session, tmp_path)
    source = local._path(storage.content_key(documents.UPLOADS, org_id, sha))
    source.unlink()
    source.mkdir()  # Real IsADirectoryError, independent of process permissions.
    temporary_paths = []
    if kind == "workspace":
        from app.services import workspace_export

        original_mkstemp = workspace_export.tempfile.mkstemp

        def tracked_mkstemp(*args, **kwargs):
            result = original_mkstemp(*args, **kwargs)
            temporary_paths.append(result[1])
            return result

        monkeypatch.setattr(workspace_export.tempfile, "mkstemp", tracked_mkstemp)
    export_id = await _request(auth_client, kind)
    failed = await _run(db_session, kind)
    assert failed.status == "queued" and failed.attempts == 1
    assert "StorageReadError" in failed.last_error
    assert str(source) not in failed.last_error
    row = await db_session.get(_model(kind), export_id)
    assert row.status == "queued" and row.sha256 is None and row.ready_at is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(EmailMessage)
            .where(EmailMessage.kind == f"{kind}_export")
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(EmailToken)
            .where(EmailToken.purpose == f"{kind}_export")
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Document).where(Document.kind == documents.EXPORTS)
        )
        == 0
    )
    if kind == "workspace":
        assert temporary_paths
        assert all(not Path(path).exists() for path in temporary_paths)
    source.rmdir()
    source.write_bytes(PAYLOAD)
    await jobs.retry(db_session, failed)
    done = await _run(db_session, kind)
    assert done.status == "succeeded", done.last_error
    await db_session.refresh(row)
    assert row.status == "ready" and row.missing_documents == 0
    assert row.sha256 is not None
    data = await documents.load(documents.EXPORTS, org_id, row.sha256)
    _assert_zip(data, kind, missing=False)
    assert row.org_id == org_id
    assert await documents.load(documents.UPLOADS, org_id, sha) == PAYLOAD


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["workspace", "archive"])
async def test_actual_missing_local_source_still_produces_explicit_missing_manifest(
    auth_client, db_session, tmp_path, kind
):
    org_id, sha, local = await _seed(auth_client, db_session, tmp_path)
    local.delete(storage.content_key(documents.UPLOADS, org_id, sha))
    export_id = await _request(auth_client, kind)
    done = await _run(db_session, kind)
    assert done.status == "succeeded", done.last_error
    row = await db_session.get(_model(kind), export_id)
    assert row.status == "ready" and row.missing_documents == 1
    data = await documents.load(documents.EXPORTS, org_id, row.sha256)
    _assert_zip(data, kind, missing=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["workspace", "archive"])
async def test_local_export_download_failure_preserves_one_time_link_and_audit_until_recovery(
    auth_client, db_session, tmp_path, kind
):
    org_id, original_sha, local = await _seed(auth_client, db_session, tmp_path)
    export_id = await _request(auth_client, kind)
    done = await _run(db_session, kind)
    assert done.status == "succeeded", done.last_error
    row = await db_session.get(_model(kind), export_id)
    export_sha, export_size = row.sha256, row.size
    email = await db_session.scalar(
        select(EmailMessage).where(EmailMessage.kind == f"{kind}_export")
    )
    match = re.search(rf"/api/v1/{kind}/export/download/([A-Za-z0-9_-]+)", email.body)
    assert match is not None
    url = match.group(0)
    token = await db_session.scalar(
        select(EmailToken).where(EmailToken.purpose == f"{kind}_export")
    )
    assert token.used_at is None
    exported = local._path(storage.content_key(documents.EXPORTS, org_id, export_sha))
    exported_bytes = exported.read_bytes()
    exported.unlink()
    exported.mkdir()
    refused = await auth_client.get(url)
    assert refused.status_code == 503, refused.text
    assert refused.json()["code"] == "storage_read_unavailable"
    assert "retry" in refused.json()["detail"]
    assert refused.headers["cache-control"] == "private, no-store"
    assert "content-disposition" not in refused.headers
    assert str(exported) not in refused.text and export_sha not in refused.text
    await db_session.refresh(row)
    await db_session.refresh(token)
    assert row.status == "ready" and row.downloaded_at is None
    assert row.sha256 == export_sha and row.size == export_size
    assert token.used_at is None
    action = (
        audit.A.WORKSPACE_EXPORT_DOWNLOADED
        if kind == "workspace"
        else audit.A.ARCHIVE_EXPORT_DOWNLOADED
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == action)
        )
        == 0
    )
    exported.rmdir()
    exported.write_bytes(exported_bytes)
    recovered = await auth_client.get(url)
    assert recovered.status_code == 200, recovered.text
    assert recovered.content == exported_bytes
    _assert_zip(recovered.content, kind, missing=False)
    await db_session.refresh(row)
    await db_session.refresh(token)
    assert row.status == "downloaded" and row.downloaded_at is not None
    assert token.used_at is not None and row.sha256 == export_sha
    assert (
        await db_session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == action)
        )
        == 1
    )
    assert (await auth_client.get(url)).status_code == 404
    assert await documents.load(documents.UPLOADS, org_id, original_sha) == PAYLOAD


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True], ids=["io-failure", "missing"])
async def test_claim_pack_refuses_read_failure_with_recovery_guidance_and_preserves_missing(
    db_session, tmp_path, missing
):
    from tests.transport.test_g2_12_claim_pack import _ready_org, _submitted_claim

    local = storage.LocalStorage(str(tmp_path / "objects"))
    storage.set_storage(local)
    org, entity = await _ready_org(db_session)
    claim, invoice, sha = await _submitted_claim(db_session, org, entity, doc_data=PAYLOAD)
    path = local._path(storage.content_key(documents.UPLOADS, org.id, sha))
    path.unlink()
    if not missing:
        path.mkdir()
    with pytest.raises(ConflictError) as error:
        await claim_pack.build_evidence_pack(db_session, org.id, claim.id)
    assert error.value.status == 409 and error.value.code == "evidence_document_unavailable"
    assert invoice.invoice_number in str(error.value)
    assert str(path) not in str(error.value)
    if missing:
        assert "missing" in str(error.value)
    else:
        assert "could not be read" in str(error.value)
        assert "retry after recovery" in str(error.value)
        assert "missing" not in str(error.value)
        path.rmdir()
    path.write_bytes(PAYLOAD)
    recovered = await claim_pack.build_evidence_pack(db_session, org.id, claim.id)
    with zipfile.ZipFile(io.BytesIO(recovered)) as zf:
        originals = [name for name in zf.namelist() if name.endswith(".pdf")]
        assert len(originals) == 1 and zf.read(originals[0]) == PAYLOAD
