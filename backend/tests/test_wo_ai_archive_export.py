"""WO-AI — the ex-client archive export (owner decision 2026-08-16 §1.C).

"An ex-client can request a one-time EXPORT of their archive; no live login is
retained." A live owner asks from the Archive screen; an ex-client's owner
asks by email from a public form. Either way the worker builds one zip and the
owner gets a ONE-TIME emailed link. Synthetic fixtures throughout.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import job as jobmodel
from app.models.archive_export import ArchiveExport
from app.models.archived_invoice import ArchivedInvoice
from app.models.email_message import EmailMessage
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.services import documents, jobs

NOW = datetime.now(UTC)
LINK = re.compile(r"/api/v1/archive/export/download/([A-Za-z0-9_\-]+)")


async def _owner(db_session) -> tuple[User, Organization]:
    user = await db_session.scalar(select(User).order_by(User.created_at).limit(1))
    org = await db_session.get(Organization, user.org_id)
    return user, org


def _row(org_id: str, number: str, *, sha: str | None, filename: str | None = None):
    return ArchivedInvoice(
        org_id=org_id,
        original_invoice_id=f"00000000-0000-0000-0000-{abs(hash(number)) % 10**12:012d}",
        invoice_number=number,
        vendor_name="Fictional Fuels OU",
        currency="EUR",
        line_items_json='[{"description": "Diesel", "quantity": 100, "line_total": "120.00"}]',
        source_sha256=sha,
        source_filename=filename,
        archived_at=NOW - timedelta(days=40),
        expires_at=NOW + timedelta(days=700),
    )


async def _seed_archive(db_session, org_id: str) -> str:
    sha, _ = await documents.store(documents.UPLOADS, org_id, b"%PDF-1.4 synthetic")
    db_session.add(_row(org_id, "ARC-0001", sha=sha, filename="depot-june.pdf"))
    db_session.add(_row(org_id, "ARC-0002", sha=None))
    db_session.add(_row(org_id, "ARC-0003", sha="f" * 64, filename="lost.pdf"))  # bytes gone
    await db_session.commit()
    return sha


async def _emailed_token(db_session, org_id: str) -> str:
    msg = await db_session.scalar(
        select(EmailMessage)
        .where(EmailMessage.org_id == org_id, EmailMessage.kind == "archive_export")
        .order_by(EmailMessage.created_at.desc())
    )
    assert msg is not None, "no archive_export email was recorded"
    m = LINK.search(msg.body or "")
    assert m, msg.body
    return m.group(1)


@pytest.mark.asyncio
async def test_wo_ai_the_owner_asks_the_worker_builds_and_the_link_works_exactly_once(
    auth_client, client, db_session
):
    user, org = await _owner(db_session)
    await _seed_archive(db_session, org.id)

    asked = await auth_client.post("/api/v1/archive/export")
    assert asked.status_code == 202, asked.text
    body = asked.json()
    assert body["status"] == "queued" and body["requested_email"] == user.email
    assert "token" not in body and "link" not in body  # the link goes by email only

    # A second ask inside the hour reuses the live request.
    again = await auth_client.post("/api/v1/archive/export")
    assert again.status_code == 202 and again.json()["id"] == body["id"]

    done = await jobs.run_once(db_session, "export-worker")
    assert done is not None and done.status == jobmodel.SUCCEEDED, done.last_error

    listed = (await auth_client.get("/api/v1/archive/export-requests")).json()
    assert listed[0]["id"] == body["id"] and listed[0]["status"] == "ready"
    assert listed[0]["records"] == 3 and listed[0]["missing_documents"] == 1

    token = await _emailed_token(db_session, org.id)
    got = await client.get(f"/api/v1/archive/export/download/{token}")
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith("application/zip")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert 'filename="archive-' in got.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(got.content)) as zf:
        names = zf.namelist()
        assert "manifest.csv" in names and "records.json" in names
        docs = [n for n in names if n.startswith("documents/")]
        assert len(docs) == 1 and docs[0].endswith("-depot-june.pdf")
        assert zf.read(docs[0]) == b"%PDF-1.4 synthetic"
        manifest = zf.read("manifest.csv").decode()
    assert "ARC-0001" in manifest and "included" in manifest
    assert "ARC-0002" in manifest and "none" in manifest
    assert "ARC-0003" in manifest and "missing" in manifest

    # ONE-TIME: the same link is dead now, indistinguishably from a wrong one.
    assert (await client.get(f"/api/v1/archive/export/download/{token}")).status_code == 404
    assert (await client.get("/api/v1/archive/export/download/not-a-token")).status_code == 404
    listed = (await auth_client.get("/api/v1/archive/export-requests")).json()
    assert listed[0]["status"] == "downloaded" and listed[0]["downloaded_at"]


@pytest.mark.asyncio
async def test_wo_ai_an_ex_client_owner_asks_by_email_and_nobody_else_learns_anything(
    auth_client, client, db_session
):
    # `auth_client` only registers the workspace; every call below is UNAUTHENTICATED.
    user, org = await _owner(db_session)
    await _seed_archive(db_session, org.id)
    org.status = "canceled"  # the client has left; no live login is retained
    await db_session.commit()

    stranger = await client.post(
        "/api/v1/archive/export/request", json={"email": "nobody@haulage.example"}
    )
    assert stranger.status_code == 200 and stranger.json() == {"sent": True}
    assert (await db_session.scalar(select(ArchiveExport))) is None

    asked = await client.post("/api/v1/archive/export/request", json={"email": user.email.upper()})
    assert asked.status_code == 200 and asked.json() == {"sent": True}  # the same answer
    row = await db_session.scalar(select(ArchiveExport))
    assert row is not None and row.org_id == org.id and row.status == "queued"

    done = await jobs.run_once(db_session, "export-worker")
    assert done is not None and done.status == jobmodel.SUCCEEDED, done.last_error
    token = await _emailed_token(db_session, org.id)
    got = await client.get(f"/api/v1/archive/export/download/{token}")
    assert got.status_code == 200
    with zipfile.ZipFile(io.BytesIO(got.content)) as zf:
        assert "ARC-0001" in zf.read("manifest.csv").decode()


@pytest.mark.asyncio
async def test_wo_ai_only_the_owner_may_export_and_the_link_expires(auth_client, db_session):
    user, org = await _owner(db_session)
    user.role = UserRole.admin
    await db_session.commit()
    refused = await auth_client.post("/api/v1/archive/export")
    assert refused.status_code == 403
    user.role = UserRole.owner
    await db_session.commit()

    asked = await auth_client.post("/api/v1/archive/export")
    assert asked.status_code == 202
    done = await jobs.run_once(db_session, "export-worker")
    assert done is not None and done.status == jobmodel.SUCCEEDED, done.last_error
    token = await _emailed_token(db_session, org.id)

    # Age the token past its seven days: the link is dead before it was ever used.
    from app.models.email_token import EmailToken

    for t in await db_session.scalars(
        select(EmailToken).where(EmailToken.purpose == "archive_export")
    ):
        t.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    assert (await auth_client.get(f"/api/v1/archive/export/download/{token}")).status_code == 404
