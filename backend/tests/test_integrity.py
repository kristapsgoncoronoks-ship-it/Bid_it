"""Document-integrity verification (Phase 2.8): re-hash stored documents against
their recorded sha256 to detect corruption or loss."""

import pytest

from app.core import storage
from app.services import documents

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "email": "b@i.test",
}


async def _upload_logo(auth_client) -> None:
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    up = await auth_client.post(
        "/api/v1/issuer/logo", files={"file": ("logo.png", _PNG, "image/png")}
    )
    assert up.status_code == 200


@pytest.mark.asyncio
async def test_verify_passes_when_intact(auth_client):
    await _upload_logo(auth_client)
    r = await auth_client.post("/api/v1/integrity/documents/verify")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked"] == 1 and body["ok"] == 1
    assert body["healthy"] is True and body["issues"] == []


@pytest.mark.asyncio
async def test_verify_detects_missing_object(auth_client, db_session):
    from sqlalchemy import select

    from app.models.issuer import IssuerProfile

    await _upload_logo(auth_client)
    profile = await db_session.scalar(select(IssuerProfile))
    org_id = profile.org_id
    # Simulate object loss: delete it from storage behind the DB reference.
    key = storage.content_key(documents.LOGOS, org_id, profile.logo_sha256)
    storage.get_storage().delete(key)

    r = await auth_client.post("/api/v1/integrity/documents/verify")
    body = r.json()
    assert body["healthy"] is False
    assert body["issues"][0]["kind"] == "logo"
    assert body["issues"][0]["problem"] == "missing"


@pytest.mark.asyncio
async def test_verify_detects_corruption(auth_client, db_session):
    from sqlalchemy import select

    from app.models.issuer import IssuerProfile

    await _upload_logo(auth_client)
    profile = await db_session.scalar(select(IssuerProfile))
    # Simulate silent corruption: overwrite the object with different bytes at the
    # same key (its content no longer matches the recorded sha256).
    key = storage.content_key(documents.LOGOS, profile.org_id, profile.logo_sha256)
    storage.get_storage().put(key, b"corrupted-bytes")

    r = await auth_client.post("/api/v1/integrity/documents/verify")
    body = r.json()
    assert body["healthy"] is False
    assert body["issues"][0]["problem"] == "mismatch"


async def _upload_invoice(auth_client) -> bytes:
    """Upload one supplier-invoice CSV; its original bytes land in the `uploads`
    store keyed by sha256 (the legal record the sweep must cover)."""
    import io

    data = b"description,quantity,unit_price,invoice_number,vendor\nA,1,10,INV-INT,Acme\n"
    r = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("a.csv", io.BytesIO(data), "text/csv")}
    )
    assert r.status_code == 202, r.text
    return data


@pytest.mark.asyncio
async def test_verify_covers_original_uploads(auth_client, db_session):
    await _upload_invoice(auth_client)

    r = await auth_client.post("/api/v1/integrity/documents/verify")
    body = r.json()
    # The stored original is swept alongside every other reference and is intact.
    assert body["healthy"] is True
    assert body["checked"] >= 1 and body["issues"] == []


@pytest.mark.asyncio
async def test_verify_detects_lost_original_upload(auth_client, db_session):
    from sqlalchemy import select

    from app.models.organization import Organization

    data = await _upload_invoice(auth_client)
    org_id = await db_session.scalar(select(Organization.id))
    # Simulate loss of the original supplier-invoice bytes behind the DB reference.
    key = storage.content_key(documents.UPLOADS, org_id, storage.sha256_hex(data))
    storage.get_storage().delete(key)

    r = await auth_client.post("/api/v1/integrity/documents/verify")
    body = r.json()
    assert body["healthy"] is False
    upload_issues = [i for i in body["issues"] if i["kind"] == "upload"]
    assert upload_issues and upload_issues[0]["problem"] == "missing"


@pytest.mark.asyncio
async def test_verify_requires_admin(auth_client, db_session):
    from sqlalchemy import select, update

    from app.models.organization import Organization
    from app.models.user import User, UserRole

    org = await db_session.scalar(select(Organization.id))
    await db_session.execute(update(User).where(User.org_id == org).values(role=UserRole.user))
    await db_session.commit()
    r = await auth_client.post("/api/v1/integrity/documents/verify")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_integrity_job_is_enqueueable_and_runs(auth_client, db_session):

    from app.services import jobs

    await _upload_logo(auth_client)
    # Admins can enqueue the background verify.
    r = await auth_client.post("/api/v1/jobs", json={"kind": "integrity.verify_documents"})
    assert r.status_code == 201, r.text

    job = await jobs.run_once(db_session, "w")
    assert job is not None and job.status == "succeeded"
    import json as _json

    result = _json.loads(job.result_json)
    assert result["checked"] == 1 and result["healthy"] is True
