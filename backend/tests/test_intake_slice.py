"""Supplier-invoice intake vertical slice — the scenarios the build must handle.

Covers: repeated uploads (dedup + idempotency), job retries (backoff → dead-letter →
manual retry), partial extraction (low-confidence flags), corrupt files (failure
reason + re-extract), unsupported formats (415), image OCR acceptance, cross-tenant
capture access, and duplicate-invoice candidate detection (same supplier vs a
different supplier). Extraction is never assumed accurate — provenance + confidence
are asserted, not just the happy path.
"""

import io

import pytest
from sqlalchemy import func, select

from app.models import job as jobmodel
from app.models.document import Document
from app.models.extraction_run import ExtractionRun
from app.services import extraction, extraction_provider, jobs

# --- a deliberately-failing job handler, for the retry/DLQ test -----------------
FLAKY_KIND = "test.intake.flaky"


@jobs.handler(FLAKY_KIND)
async def _flaky(db, payload, job):  # noqa: ANN001, ARG001 - test handler
    raise RuntimeError("boom")


def _file(name: str, data: bytes, ctype: str) -> dict:
    return {"file": (name, io.BytesIO(data), ctype)}


async def _org_id(auth_client) -> str:
    return (await auth_client.get("/api/v1/auth/me")).json()["organization"]["id"]


async def _drain(db_session):
    for _ in range(50):
        if await jobs.run_once(db_session, "test-worker") is None:
            return


async def _make_invoice(auth_client, *, vendor: str, number: str) -> dict:
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": vendor,
            "invoice_number": number,
            "issue_date": "2026-05-01",
            "currency": "EUR",
            "line_items": [
                {"description": "Item", "quantity": "1", "unit_price": "100", "tax_rate": "0"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #


def test_provider_interface_selects_and_extracts():
    # The registry picks a provider by file, returns a ProviderResult, and an
    # unknown type raises (deterministic, no I/O).
    csv = b"description,quantity,unit_price,invoice_number\nFuel,2,10,INV-P1\n"
    result = extraction_provider.run("x.csv", csv)
    assert result.method == "csv" and result.provider == "csv"
    # Header captures (line_index None) are the five top-level fields; the row
    # additionally yields six line-scoped captures (E1.2 — the old set-equality
    # here encoded the header-only limitation).
    assert {f.field for f in result.fields if f.line_index is None} == {
        "invoice_number",
        "vendor_name",
        "issue_date",
        "due_date",
        "currency",
    }
    assert {f.field for f in result.fields if f.line_index == 0} == {
        "description",
        "category",
        "quantity",
        "unit_price",
        "amount",
        "tax_rate",
    }
    # Providers are pluggable + ordered.
    names = [p.name for p in extraction_provider.providers()]
    assert {"pdf", "einvoice", "csv", "json", "image-ocr"} <= set(names)
    with pytest.raises(ValueError):
        extraction_provider.run("weird.bin", b"nope")


# --------------------------------------------------------------------------- #
# 1. Repeated uploads — dedup at storage + idempotent capture
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_repeated_upload_dedups_and_is_idempotent(auth_client, db_session):
    data = b"description,quantity,unit_price,invoice_number,vendor\nA,1,10,INV-DUP,Acme\n"
    org = await _org_id(auth_client)

    r1 = await auth_client.post("/api/v1/invoices/upload", files=_file("a.csv", data, "text/csv"))
    # E1.3: a second byte-identical upload is now an advisory re-upload guard —
    # override=true is the explicit escape hatch (see test_duplicate_upload_*
    # below for the guard itself). Passing it here preserves this test's original
    # purpose: storage dedups and the job-enqueue key is idempotent, regardless of
    # how many ExtractionRun rows exist.
    r2 = await auth_client.post(
        "/api/v1/invoices/upload",
        files=_file("a.csv", data, "text/csv"),
        params={"override": "true"},
    )
    assert r1.status_code == 202 and r2.status_code == 202

    # The original bytes are stored ONCE (content-addressed dedup), even though two
    # capture runs were created.
    sha = extraction.sha256_hex(data)
    count = await db_session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.org_id == org, Document.sha256 == sha, Document.kind == "uploads")
    )
    assert count == 1
    runs = await db_session.scalar(
        select(func.count()).select_from(ExtractionRun).where(ExtractionRun.org_id == org)
    )
    assert runs == 2  # two upload attempts = two runs, sharing the one stored file

    # The enqueue is idempotent per run: re-enqueuing the same run's key is a no-op.
    j1 = await jobs.enqueue(
        db_session,
        extraction.UPLOAD_EXTRACT_KIND,
        {"run_id": "x"},
        org_id=org,
        idempotency_key="upload-extract:x",
    )
    j2 = await jobs.enqueue(
        db_session,
        extraction.UPLOAD_EXTRACT_KIND,
        {"run_id": "x"},
        org_id=org,
        idempotency_key="upload-extract:x",
    )
    assert j1.id == j2.id


# --------------------------------------------------------------------------- #
# 1b. Hash-based re-upload detection (E1.3) — advisory, overridable
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_duplicate_upload_blocked_without_override(auth_client, db_session):
    data = b"description,quantity,unit_price,invoice_number\nA,1,10,INV-DUP2\n"
    org = await _org_id(auth_client)

    r1 = await auth_client.post("/api/v1/invoices/upload", files=_file("a.csv", data, "text/csv"))
    assert r1.status_code == 202

    r2 = await auth_client.post("/api/v1/invoices/upload", files=_file("a.csv", data, "text/csv"))
    assert r2.status_code == 409
    body = r2.json()
    assert body["code"] == "duplicate_upload"
    assert "already uploaded" in body["detail"]

    # The blocked attempt created NOTHING — no second run, no second stored file.
    runs = await db_session.scalar(
        select(func.count()).select_from(ExtractionRun).where(ExtractionRun.org_id == org)
    )
    assert runs == 1
    sha = extraction.sha256_hex(data)
    docs = await db_session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.org_id == org, Document.sha256 == sha, Document.kind == "uploads")
    )
    assert docs == 1


@pytest.mark.asyncio
async def test_duplicate_upload_override_allowed(auth_client, db_session):
    data = b"description,quantity,unit_price,invoice_number\nA,1,10,INV-DUP3\n"
    org = await _org_id(auth_client)

    r1 = await auth_client.post("/api/v1/invoices/upload", files=_file("a.csv", data, "text/csv"))
    assert r1.status_code == 202

    r2 = await auth_client.post(
        "/api/v1/invoices/upload",
        files=_file("a.csv", data, "text/csv"),
        params={"override": "true"},
    )
    assert r2.status_code == 202

    runs = await db_session.scalar(
        select(func.count()).select_from(ExtractionRun).where(ExtractionRun.org_id == org)
    )
    assert runs == 2


@pytest.mark.asyncio
async def test_failed_upload_is_not_a_duplicate(auth_client, db_session):
    # Structurally valid JSON that isn't an invoice object → the parse fails
    # (mirrors test_corrupt_file_fails_with_reason_and_can_be_retried). A FAILED
    # capture didn't actually capture anything, so re-uploading the same bytes is
    # not "the same document already on file" and needs no override.
    r1 = await auth_client.post(
        "/api/v1/invoices/upload", files=_file("bad.json", b"[]", "application/json")
    )
    assert r1.status_code == 202
    await _drain(db_session)
    run1 = await db_session.scalar(
        select(ExtractionRun).where(ExtractionRun.id == r1.json()["extraction_run_id"])
    )
    assert run1.status == "failed"

    r2 = await auth_client.post(
        "/api/v1/invoices/upload", files=_file("bad.json", b"[]", "application/json")
    )
    assert r2.status_code == 202  # no override needed — the prior attempt failed

    org = await _org_id(auth_client)
    runs = await db_session.scalar(
        select(func.count()).select_from(ExtractionRun).where(ExtractionRun.org_id == org)
    )
    assert runs == 2


@pytest.mark.asyncio
async def test_duplicate_upload_is_tenant_scoped(auth_client, client):
    data = b"description,quantity,unit_price,invoice_number\nA,1,10,INV-DUP4\n"
    r1 = await auth_client.post("/api/v1/invoices/upload", files=_file("a.csv", data, "text/csv"))
    assert r1.status_code == 202

    # A second, unrelated org uploads byte-IDENTICAL content — the guard is
    # per-tenant, so this must succeed with no override, never see org A's run.
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Gamma",
            "name": "G",
            "email": "g@gamma.io",
            "password": "supersecret",
        },
    )
    hb = {"Authorization": f"Bearer {reg.json()['token']['access_token']}"}
    r2 = await client.post(
        "/api/v1/invoices/upload", files=_file("a.csv", data, "text/csv"), headers=hb
    )
    assert r2.status_code == 202


# --------------------------------------------------------------------------- #
# 2. Job retries — backoff, dead-letter, manual retry
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_job_retry_backoff_then_dead_letter_then_manual_retry(auth_client, db_session):
    org = await _org_id(auth_client)
    await _drain(db_session)  # clear any pre-existing jobs

    job = await jobs.enqueue(db_session, FLAKY_KIND, {}, org_id=org, max_attempts=2)

    # First failure → requeued with a future run_after (backoff), attempts incremented.
    await jobs.run_once(db_session, "w")
    await db_session.refresh(job)
    assert job.status == jobmodel.QUEUED and job.attempts == 1 and job.last_error

    # Make it ready again → second failure hits max_attempts → dead-lettered.
    job.run_after = job.run_after.replace(year=2000)
    await db_session.commit()
    await jobs.run_once(db_session, "w")
    await db_session.refresh(job)
    assert job.status == jobmodel.DEAD and job.attempts == 2 and "boom" in (job.last_error or "")

    # Manual retry brings it back off the dead-letter queue.
    await jobs.retry(db_session, job)
    await db_session.refresh(job)
    assert job.status == jobmodel.QUEUED


# --------------------------------------------------------------------------- #
# 3. Partial extraction — low-confidence / missing flags, not a pretend-perfect draft
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_partial_extraction_flags_missing_fields(auth_client, parse_upload):
    # Lines present, but vendor + due_date absent → the draft parses, and the
    # provenance says which fields need a human.
    csv = b"description,quantity,unit_price,invoice_number\nFuel,10,1.50,INV-PART\n"
    draft = await parse_upload(auth_client, _file("p.csv", csv, "text/csv"))
    prov = {f["field"]: f for f in draft["fields"]}
    assert prov["vendor_name"]["status"] == "missing"
    assert prov["vendor_name"]["low_confidence"] is True  # flagged for review
    assert prov["currency"]["status"] == "defaulted"  # EUR default
    assert prov["invoice_number"]["status"] == "extracted"
    assert prov["invoice_number"]["low_confidence"] is False
    # Deterministic parser → exact, so confidence is None (not a fake number).
    assert all(f["confidence"] is None for f in draft["fields"])
    assert prov["invoice_number"]["provider"] == "csv"


# --------------------------------------------------------------------------- #
# 4. Corrupt files — failure reason + manual re-extract
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_corrupt_file_fails_with_reason_and_can_be_retried(auth_client, db_session):
    # Valid JSON structurally (passes the security gate) but not an invoice object
    # → the parse fails with a clear reason on the run.
    r = await auth_client.post(
        "/api/v1/invoices/upload", files=_file("bad.json", b"[]", "application/json")
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["extraction_run_id"]
    await _drain(db_session)

    res = (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()
    assert res["status"] == "failed"
    assert res["error"] and "object" in res["error"].lower()

    # Manual re-extract re-queues the same stored bytes and runs again.
    retry = await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry")
    assert retry.status_code == 202
    # The retry runs in the request's OWN session; drop this session's cached copy
    # (loaded as "failed" during _drain) so we read the freshly-committed state.
    db_session.expire_all()
    ran = await db_session.scalar(select(ExtractionRun).where(ExtractionRun.id == run_id))
    assert ran.status == "queued"  # reset for the re-run
    await _drain(db_session)
    res2 = (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()
    assert res2["status"] == "failed"  # same bad bytes → fails again, but it re-ran


@pytest.mark.asyncio
async def test_corrupt_pdf_fails_gracefully(auth_client, db_session):
    # A file that claims to be a PDF (passes the magic check) but is garbage → the
    # run fails with a reason, whether or not the OCR stack is installed.
    r = await auth_client.post(
        "/api/v1/invoices/upload",
        files=_file("x.pdf", b"%PDF-1.4\nnot really a pdf", "application/pdf"),
    )
    assert r.status_code == 202
    run_id = r.json()["extraction_run_id"]
    await _drain(db_session)
    res = (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()
    # Handled gracefully to a TERMINAL state (never a 500 / stuck run): either a
    # failed run with a reason, or an empty-but-parsed draft — depending on whether
    # the OCR stack can open the garbage bytes.
    assert res["status"] in ("failed", "parsed")
    if res["status"] == "failed":
        assert res["error"]


# --------------------------------------------------------------------------- #
# 5. Unsupported formats — rejected at the gate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unsupported_format_rejected(auth_client):
    r = await auth_client.post(
        "/api/v1/invoices/upload", files=_file("notes.txt", b"just some text", "text/plain")
    )
    assert r.status_code == 415


# --------------------------------------------------------------------------- #
# 6. Image OCR inputs are ACCEPTED (JPEG/PNG are supported inputs)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_png_image_is_accepted_not_rejected(auth_client, db_session):
    # A minimal valid PNG header + body — accepted (202), never a 415. OCR may or
    # may not be installed; either way the format is supported.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x00\x03\x00\x01\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    r = await auth_client.post("/api/v1/invoices/upload", files=_file("scan.png", png, "image/png"))
    assert r.status_code == 202, r.text
    await _drain(db_session)
    res = (await auth_client.get(f"/api/v1/invoices/upload/{r.json()['extraction_run_id']}")).json()
    # Parsed (if OCR available) or failed with a reason (if not) — but accepted.
    assert res["status"] in ("parsed", "failed")


# --------------------------------------------------------------------------- #
# 7. Cross-tenant capture access — org B cannot see org A's upload
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cross_tenant_capture_access_denied(auth_client, client):
    up = await auth_client.post(
        "/api/v1/invoices/upload",
        files=_file("a.csv", b"description,unit_price\nA,10\n", "text/csv"),
    )
    run_id = up.json()["extraction_run_id"]

    # A second org.
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Beta",
            "name": "B",
            "email": "b@beta.io",
            "password": "supersecret",
        },
    )
    tok_b = reg.json()["token"]["access_token"]
    hb = {"Authorization": f"Bearer {tok_b}"}

    assert (await client.get(f"/api/v1/invoices/upload/{run_id}", headers=hb)).status_code == 404
    assert (
        await client.post(f"/api/v1/invoices/upload/{run_id}/retry", headers=hb)
    ).status_code == 404


# --------------------------------------------------------------------------- #
# 8. Duplicate invoice numbers — same supplier vs different suppliers
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_duplicate_same_supplier(auth_client):
    a = await _make_invoice(auth_client, vendor="Neste", number="INV-777")
    vendor_id = a["vendor_id"]
    # A second invoice, same supplier + same number.
    await _make_invoice(auth_client, vendor="Neste", number="INV-777")

    rep = (
        await auth_client.get(
            "/api/v1/invoices/duplicate-candidates",
            params={"invoice_number": "INV-777", "vendor_id": vendor_id},
        )
    ).json()
    assert len(rep["exact"]) == 2  # both same-supplier invoices
    assert rep["cross_supplier"] == []


@pytest.mark.asyncio
async def test_same_number_different_suppliers(auth_client):
    a = await _make_invoice(auth_client, vendor="Circle K", number="INV-SAME")
    await _make_invoice(auth_client, vendor="Shell", number="INV-SAME")

    rep = (
        await auth_client.get(
            "/api/v1/invoices/duplicate-candidates",
            params={"invoice_number": "INV-SAME", "vendor_id": a["vendor_id"]},
        )
    ).json()
    # Same number under Circle K = exact; under Shell = a cross-supplier candidate.
    assert [c["vendor_name"] for c in rep["exact"]] == ["Circle K"]
    assert [c["vendor_name"] for c in rep["cross_supplier"]] == ["Shell"]


@pytest.mark.asyncio
async def test_cross_supplier_validation_warning(auth_client, db_session):
    """The deterministic validator emits `duplicate_cross_supplier` (warning) when
    the same number exists under a different supplier — not the same-supplier error."""
    from datetime import date

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.invoice import Invoice
    from app.services import validation

    await _make_invoice(auth_client, vendor="AlphaCo", number="X-1")
    b = await _make_invoice(auth_client, vendor="BetaCo", number="X-1")

    inv_b = await db_session.scalar(
        select(Invoice).where(Invoice.id == b["id"]).options(selectinload(Invoice.line_items))
    )
    findings = await validation.run_checks(db_session, inv_b, date(2026, 5, 1))
    codes = {f.code for f in findings}
    assert "duplicate_cross_supplier" in codes
    assert "duplicate" not in codes  # not a same-supplier duplicate
