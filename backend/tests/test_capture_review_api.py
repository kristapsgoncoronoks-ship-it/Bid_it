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
    # Header rows (line_index None) are the five top-level fields; the 1-line CSV
    # additionally carries 6 line-scoped rows (E1.2 — the old set-equality here
    # encoded the header-only limitation).
    fields = {f["field"]: f for f in r.json() if f["line_index"] is None}
    assert set(fields) == {"invoice_number", "vendor_name", "issue_date", "due_date", "currency"}
    line0 = {f["field"]: f for f in r.json() if f["line_index"] == 0}
    assert set(line0) == {"description", "category", "quantity", "unit_price", "amount", "tax_rate"}
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


# --------------------------------------------------------------------------- #
# E1.5 — extraction learning loop (per-vendor x field advisory hint)
# --------------------------------------------------------------------------- #

_CSV_SAME_VENDOR_2 = (
    "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    "Fictional Fuels OU,INV-CAP-2,2026-06-08,Diesel,12,1.55,18.60,21\n"
)

_CSV_OTHER_VENDOR = (
    "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    "Другой Vendor OU,INV-CAP-3,2026-06-09,Diesel,8,1.40,11.20,21\n"
)


@pytest.mark.asyncio
async def test_correction_on_one_capture_hints_the_next_same_vendor_capture(
    auth_client, parse_upload
):
    """E1.5's own acceptance line, end to end: a field corrected during review
    of one capture is proposed (`suggested_value`) on the NEXT capture from the
    same vendor — and the suggestion NEVER lands in `value`/`reviewed_value` on
    that new capture (§4.19: a suggestion, never an auto-apply)."""
    run1 = await _parsed_run_id(auth_client, parse_upload)
    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run1}/review",
        json={"fields": [{"field": "vendor_name", "reviewed_value": "Fictional Fuels OÜ"}]},
    )
    assert r.status_code == 200, r.text

    up2 = await parse_upload(
        auth_client, {"file": ("cap2.csv", io.BytesIO(_CSV_SAME_VENDOR_2.encode()), "text/csv")}
    )
    run2 = up2["extraction_run_id"]

    rows = (await auth_client.get(f"/api/v1/invoices/captures/{run2}/fields")).json()
    vendor_field = next(f for f in rows if f["field"] == "vendor_name" and f["line_index"] is None)
    assert vendor_field["suggested_value"] == "Fictional Fuels OÜ"
    assert vendor_field["suggestion_corrected_count"] == 1
    # The §4.19 proof: the hint is a SEPARATE field — the run's OWN capture is
    # untouched by it.
    assert vendor_field["value"] == "Fictional Fuels OU"
    assert vendor_field["reviewed_value"] is None

    # The POST .../review response carries the same hint (read-only, additive).
    r2 = await auth_client.post(f"/api/v1/invoices/captures/{run2}/review", json={"fields": []})
    assert r2.status_code == 200, r2.text
    vendor_field2 = next(
        f for f in r2.json() if f["field"] == "vendor_name" and f["line_index"] is None
    )
    assert vendor_field2["suggested_value"] == "Fictional Fuels OÜ"
    assert vendor_field2["value"] == "Fictional Fuels OU"
    assert vendor_field2["reviewed_value"] is None


@pytest.mark.asyncio
async def test_hint_absent_for_a_different_vendor(auth_client, parse_upload):
    """A capture from a DIFFERENT vendor shows no hint for the same field name —
    the memory is scoped per exact vendor, never blended across vendors."""
    run1 = await _parsed_run_id(auth_client, parse_upload)
    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run1}/review",
        json={"fields": [{"field": "vendor_name", "reviewed_value": "Fictional Fuels OÜ"}]},
    )
    assert r.status_code == 200, r.text

    up_other = await parse_upload(
        auth_client, {"file": ("other.csv", io.BytesIO(_CSV_OTHER_VENDOR.encode()), "text/csv")}
    )
    rows = (
        await auth_client.get(f"/api/v1/invoices/captures/{up_other['extraction_run_id']}/fields")
    ).json()
    vendor_field = next(f for f in rows if f["field"] == "vendor_name" and f["line_index"] is None)
    assert vendor_field["suggested_value"] is None
    assert vendor_field["suggestion_corrected_count"] is None


@pytest.mark.asyncio
async def test_hint_not_recorded_on_same_value_resubmit(auth_client, parse_upload, db_session):
    """Resubmitting a field's EXISTING value through `.../review` is still
    accepted and audited (unchanged behaviour), but does not inflate
    `corrected_count` — a no-op confirm is not evidence the parser was wrong."""
    from app.models.capture_field_memory import CaptureFieldMemory

    run_id = await _parsed_run_id(auth_client, parse_upload)
    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run_id}/review",
        json={"fields": [{"field": "invoice_number", "reviewed_value": "INV-CAP-1"}]},
    )
    assert r.status_code == 200, r.text  # same value as the parser captured

    row = await db_session.scalar(
        select(CaptureFieldMemory).where(CaptureFieldMemory.field == "invoice_number")
    )
    # `observe_fields` (parse time) DOES create the row (it's the accuracy
    # denominator); the assertion is that a same-value resubmit never bumps
    # `corrected_count` off that baseline.
    assert row is not None
    assert row.corrected_count == 0, "a same-value resubmit must not count as a correction"
    assert row.last_corrected_value is None


@pytest.mark.asyncio
async def test_hint_cross_tenant_isolated(auth_client, parse_upload, client):
    """Org A corrects a field for a vendor; org B, with an IDENTICALLY named
    vendor and an identical capture, sees NO hint (§4.1 — the ORM tenant guard
    scopes `capture_field_memory` exactly like every other tenant table)."""
    run1 = await _parsed_run_id(auth_client, parse_upload)
    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run1}/review",
        json={"fields": [{"field": "vendor_name", "reviewed_value": "Fictional Fuels OÜ"}]},
    )
    assert r.status_code == 200, r.text

    # `client` IS the same AsyncClient `auth_client` wraps — re-register on it to
    # switch tenants (mirrors `test_capture_endpoints_cross_tenant_404` above).
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Tenant Capture BV",
            "name": "Owner B",
            "email": "owner-b-capture-memory@example.com",
            "password": "supersecret",
        },
    )
    assert reg.status_code == 201, reg.text
    client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"

    # Org B parses the SAME bytes — an identically-spelled vendor, identical
    # invoice_number — a fresh org, so no duplicate-upload collision either.
    up_b = await parse_upload(client, _files())
    run_id_b = up_b["extraction_run_id"]
    rows_b = (await client.get(f"/api/v1/invoices/captures/{run_id_b}/fields")).json()
    vendor_field_b = next(
        f for f in rows_b if f["field"] == "vendor_name" and f["line_index"] is None
    )
    assert vendor_field_b["suggested_value"] is None
    assert vendor_field_b["suggestion_corrected_count"] is None


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


# --------------------------------------------------------------------------- #
# E1.2 — line-scoped corrections
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_line_field_correction_roundtrip_and_audit(auth_client, parse_upload, db_session):
    """A correction targeting (field, line_index) persists on exactly that row —
    a body item WITHOUT line_index never hits a line row — and is audited with
    the `line_items[0].amount` meta key (§4.16)."""
    from app.models.audit import AuditEvent

    run_id = await _parsed_run_id(auth_client, parse_upload)

    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run_id}/review",
        json={"fields": [{"field": "amount", "line_index": 0, "reviewed_value": "16.00"}]},
    )
    assert r.status_code == 200, r.text

    rows = (await auth_client.get(f"/api/v1/invoices/captures/{run_id}/fields")).json()
    line0 = {f["field"]: f for f in rows if f["line_index"] == 0}
    assert line0["amount"]["reviewed_value"] == "16.00"
    assert line0["amount"]["low_confidence"] is False
    assert line0["amount"]["value"] == "15.00"  # the capture is kept, not rewritten
    # No header row was touched (there is no header "amount"), and no other line
    # cell picked up the correction.
    assert all(
        f["reviewed_value"] is None
        for f in rows
        if not (f["line_index"] == 0 and f["field"] == "amount")
    )

    ev = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "capture.field_review", AuditEvent.target_id == run_id
        )
    )
    assert ev is not None
    meta = json.loads(ev.meta)
    assert meta["fields"]["line_items[0].amount"] == {"old": "15.00", "new": "16.00"}


@pytest.mark.asyncio
async def test_line_correction_unknown_index_ignored(auth_client, parse_upload, db_session):
    """An out-of-range line_index mutates nothing and writes no audit event —
    same contract as an unknown header field (§8 malformed boundary)."""
    from app.models.audit import AuditEvent

    run_id = await _parsed_run_id(auth_client, parse_upload)
    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run_id}/review",
        json={"fields": [{"field": "amount", "line_index": 99, "reviewed_value": "1.00"}]},
    )
    assert r.status_code == 200, r.text
    rows = (await auth_client.get(f"/api/v1/invoices/captures/{run_id}/fields")).json()
    assert all(f["reviewed_value"] is None for f in rows)
    ev = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "capture.field_review", AuditEvent.target_id == run_id
        )
    )
    assert ev is None
