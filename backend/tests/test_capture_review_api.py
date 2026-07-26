"""Capture-review API surface for the E1.1 extraction-review UI (WO-12).

Covers the two additive read endpoints — the LIVE per-field provenance rows
(`GET /invoices/captures/{run_id}/fields`, so a reload after a correction still
shows the reviewed values) and the inert source-document download
(`GET /invoices/captures/{run_id}/source`, the side-by-side pane) — plus the
§4.16 fix: the correction POST now writes a `capture.field_review` audit event
with old→new meta, atomically with the field update.

Adversarial coverage per §8: cross-tenant by-id → opaque 404 (never 403),
unauthenticated → 401, and the no-stored-source boundary case.
"""

import io
import json

import pytest
from sqlalchemy import select

_CSV = (
    "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    "Fictional Fuels OU,INV-CAP-1,2026-06-01,Diesel,10,1.50,15.00,21\n"
)


def _files(name: str = "cap.csv") -> dict:
    return {"file": (name, io.BytesIO(_CSV.encode()), "text/csv")}


async def _parsed_run_id(auth_client, parse_upload) -> str:
    up = await parse_upload(auth_client, _files())
    return up["extraction_run_id"]


@pytest.mark.asyncio
async def test_capture_fields_live_provenance_roundtrip(auth_client, parse_upload):
    """GET fields returns the live rows; a correction persists `reviewed_value`
    and clears `low_confidence`, and a subsequent GET (a page reload) sees it."""
    run_id = await _parsed_run_id(auth_client, parse_upload)

    r = await auth_client.get(f"/api/v1/invoices/captures/{run_id}/fields")
    assert r.status_code == 200, r.text
    fields = {f["field"]: f for f in r.json()}
    assert set(fields) == {"invoice_number", "vendor_name", "issue_date", "due_date", "currency"}
    assert fields["vendor_name"]["status"] == "extracted"
    assert fields["vendor_name"]["reviewed_value"] is None

    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run_id}/review",
        json={"fields": [{"field": "vendor_name", "reviewed_value": "Fictional Fuels OÜ"}]},
    )
    assert r.status_code == 200, r.text

    r = await auth_client.get(f"/api/v1/invoices/captures/{run_id}/fields")
    assert r.status_code == 200
    after = {f["field"]: f for f in r.json()}
    assert after["vendor_name"]["reviewed_value"] == "Fictional Fuels OÜ"
    assert after["vendor_name"]["low_confidence"] is False
    # The captured original stays intact next to the human correction.
    assert after["vendor_name"]["value"] == "Fictional Fuels OU"


@pytest.mark.asyncio
async def test_capture_source_serves_original_bytes(auth_client, parse_upload):
    run_id = await _parsed_run_id(auth_client, parse_upload)
    r = await auth_client.get(f"/api/v1/invoices/captures/{run_id}/source")
    assert r.status_code == 200, r.text
    assert r.content == _CSV.encode()
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" in r.headers
    assert r.headers["content-type"].startswith("text/csv")


@pytest.mark.asyncio
async def test_capture_endpoints_cross_tenant_404(auth_client, parse_upload, client):
    """Org B with org A's run id gets an OPAQUE 404 on every capture endpoint —
    never a 403 that would confirm the id exists (§4.4)."""
    run_id = await _parsed_run_id(auth_client, parse_upload)

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Tenant BV",
            "name": "Owner B",
            "email": "owner-b-capture@example.com",
            "password": "supersecret",
        },
    )
    assert reg.status_code == 201, reg.text
    headers = {"Authorization": f"Bearer {reg.json()['token']['access_token']}"}

    for path in (
        f"/api/v1/invoices/captures/{run_id}/fields",
        f"/api/v1/invoices/captures/{run_id}/source",
    ):
        r = await client.get(path, headers=headers)
        assert r.status_code == 404, f"{path}: {r.status_code} {r.text}"
    r = await client.post(
        f"/api/v1/invoices/captures/{run_id}/review",
        json={"fields": [{"field": "vendor_name", "reviewed_value": "pwned"}]},
        headers=headers,
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_capture_endpoints_require_auth(auth_client, parse_upload, client):
    """No token → 401. (INVOICE_READ is deliberately held by every business role —
    capture is the metered flow — so the denied case is the unauthenticated one.)"""
    run_id = await _parsed_run_id(auth_client, parse_upload)
    # `auth_client` IS `client` with a default bearer header — drop it so these
    # requests are genuinely anonymous.
    del client.headers["Authorization"]
    for path in (
        f"/api/v1/invoices/captures/{run_id}/fields",
        f"/api/v1/invoices/captures/{run_id}/source",
    ):
        r = await client.get(path)
        assert r.status_code == 401, f"{path}: {r.status_code}"


@pytest.mark.asyncio
async def test_capture_review_correction_is_audited(auth_client, parse_upload, db_session):
    """§4.16: the correction POST records a `capture.field_review` event with
    old→new per field, in the same transaction as the field update."""
    from app.models.audit import AuditEvent

    run_id = await _parsed_run_id(auth_client, parse_upload)
    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run_id}/review",
        json={"fields": [{"field": "invoice_number", "reviewed_value": "INV-CAP-1-FIXED"}]},
    )
    assert r.status_code == 200, r.text

    ev = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "capture.field_review", AuditEvent.target_id == run_id
        )
    )
    assert ev is not None, "correction was not audited"
    assert ev.target_type == "extraction_run"
    meta = json.loads(ev.meta)
    assert meta["fields"]["invoice_number"]["old"] == "INV-CAP-1"
    assert meta["fields"]["invoice_number"]["new"] == "INV-CAP-1-FIXED"


@pytest.mark.asyncio
async def test_capture_review_unknown_field_not_audited(auth_client, parse_upload, db_session):
    """A correction naming only unknown fields changes nothing — and writes no
    audit event (no mutation happened)."""
    from app.models.audit import AuditEvent

    run_id = await _parsed_run_id(auth_client, parse_upload)
    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run_id}/review",
        json={"fields": [{"field": "no_such_field", "reviewed_value": "x"}]},
    )
    assert r.status_code == 200, r.text
    ev = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "capture.field_review", AuditEvent.target_id == run_id
        )
    )
    assert ev is None


@pytest.mark.asyncio
async def test_capture_source_missing_sha_404(auth_client, db_session):
    """A run with no stored source (e.g. a legacy synchronous parse) → opaque 404."""
    from app.services import extraction

    org_id = (await auth_client.get("/api/v1/auth/me")).json()["organization"]["id"]
    run = await extraction.record(
        db_session, org_id, filename=None, sha256=None, method="csv", status="parsed"
    )
    r = await auth_client.get(f"/api/v1/invoices/captures/{run.id}/source")
    assert r.status_code == 404, r.text
