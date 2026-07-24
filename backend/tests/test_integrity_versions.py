"""Document-version integrity (Slice 5g): the integrity check guards the
supersession-chain invariants — exactly one current version per slot, the current
version's sha matches the owner's live pointer, and no stored file lacks a
history."""

import json as _json

import pytest
from sqlalchemy import select, update

from app.models.document_version import DocumentVersion

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)
_PNG2 = _PNG[:-4] + b"ALT2"  # different bytes → different sha → a real v2


async def _upload_logo(auth_client, blob=_PNG) -> None:
    up = await auth_client.post(
        "/api/v1/issuer/logo", files={"file": ("logo.png", blob, "image/png")}
    )
    assert up.status_code == 200, up.text


@pytest.mark.asyncio
async def test_versions_verify_passes_when_intact(auth_client):
    await _upload_logo(auth_client)
    r = await auth_client.post("/api/v1/integrity/versions/verify")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked"] == 1 and body["ok"] == 1
    assert body["healthy"] is True and body["issues"] == []


@pytest.mark.asyncio
async def test_versions_detects_current_cache_mismatch(auth_client, db_session):
    await _upload_logo(auth_client)
    # Tamper the current version's sha so it disagrees with the owner's pointer.
    await db_session.execute(
        update(DocumentVersion).where(DocumentVersion.is_current.is_(True)).values(sha256="0" * 64)
    )
    await db_session.commit()

    body = (await auth_client.post("/api/v1/integrity/versions/verify")).json()
    assert body["healthy"] is False
    assert body["issues"][0]["kind"] == "document_version"
    assert body["issues"][0]["problem"] == "mismatch"


@pytest.mark.asyncio
async def test_versions_detects_two_current_rows(auth_client, db_session):
    await _upload_logo(auth_client)
    await _upload_logo(auth_client, _PNG2)  # v2 becomes current, v1 demoted
    # Corrupt the invariant: mark v1 current too, so the slot has two currents.
    await db_session.execute(
        update(DocumentVersion).where(DocumentVersion.version == 1).values(is_current=True)
    )
    await db_session.commit()

    body = (await auth_client.post("/api/v1/integrity/versions/verify")).json()
    assert body["healthy"] is False
    assert body["issues"][0]["problem"] == "current_count"


@pytest.mark.asyncio
async def test_versions_detects_missing_history(auth_client, db_session):
    await _upload_logo(auth_client)
    # Drop the history but leave the owner's live pointer set.
    from app.models.document_version import DocumentVersion as DV

    rows = list(await db_session.scalars(select(DV)))
    for row in rows:
        await db_session.delete(row)
    await db_session.commit()

    body = (await auth_client.post("/api/v1/integrity/versions/verify")).json()
    assert body["healthy"] is False
    assert body["issues"][0]["problem"] == "missing"


@pytest.mark.asyncio
async def test_versions_verify_requires_admin(auth_client, db_session):
    from app.models.organization import Organization
    from app.models.user import User, UserRole

    org = await db_session.scalar(select(Organization.id))
    await db_session.execute(update(User).where(User.org_id == org).values(role=UserRole.user))
    await db_session.commit()
    r = await auth_client.post("/api/v1/integrity/versions/verify")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_versions_job_is_enqueueable_and_runs(auth_client, db_session):
    from app.services import jobs

    await _upload_logo(auth_client)
    r = await auth_client.post("/api/v1/jobs", json={"kind": "integrity.verify_versions"})
    assert r.status_code == 201, r.text
    job = await jobs.run_once(db_session, "w")
    assert job is not None and job.status == "succeeded"
    result = _json.loads(job.result_json)
    assert result["checked"] == 1 and result["healthy"] is True
