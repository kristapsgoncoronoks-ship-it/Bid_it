"""Selected-region commands preserve originals and require explicit current review."""

import json
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.capture_region import CaptureRegionRead
from app.models.extraction_field import ExtractionField
from app.models.extraction_run import ExtractionRun
from app.models.job import Job
from app.schemas.invoice import InvoiceCreate, LineItemIn
from app.services import audit, capture_region, documents, region_ocr


async def seed(client, db):
    me = (await client.get("/api/v1/auth/me")).json()
    org_id = me["organization"]["id"]
    user_id = me["user"]["id"]
    content = b"synthetic immutable original"
    sha, _ = await documents.store(documents.UPLOADS, org_id, content, "image/png")
    draft = InvoiceCreate(
        vendor_name="Captured Vendor",
        invoice_number="BEFORE-1",
        issue_date=date(2026, 10, 21),
        currency="EUR",
        line_items=[
            LineItemIn(description="Original row", quantity="1", unit_price="10", amount="10")
        ],
    )
    run = ExtractionRun(
        org_id=org_id,
        source_filename="synthetic.png",
        source_sha256=sha,
        status="parsed",
        method="ocr",
        attempt_seq=0,
        draft_json=json.dumps(
            {"draft": draft.model_dump(mode="json"), "method": "ocr", "fields": [], "warnings": []}
        ),
        preview_json=json.dumps(
            {
                "status": "ready",
                "pages": [
                    {"number": 1, "sha256": "a" * 64, "size": 1, "width": 600, "height": 800}
                ],
                "total_pages": 1,
                "truncated": False,
            }
        ),
    )
    db.add(run)
    await db.flush()
    for field, value in [
        ("vendor_name", "Captured Vendor"),
        ("invoice_number", "BEFORE-1"),
        ("issue_date", "2026-10-21"),
        ("currency", "EUR"),
    ]:
        db.add(
            ExtractionField(
                org_id=org_id,
                extraction_run_id=run.id,
                field=field,
                value=value,
                original_value=value,
                normalized_value=value,
                status="extracted",
                provider="ocr",
                low_confidence=True,
            )
        )
    for field, value in [
        ("description", "Original row"),
        ("quantity", "1"),
        ("unit_price", "10"),
        ("amount", "10"),
        ("tax_rate", "0"),
    ]:
        db.add(
            ExtractionField(
                org_id=org_id,
                extraction_run_id=run.id,
                field=field,
                line_index=0,
                value=value,
                original_value=value,
                normalized_value=value,
                status="extracted",
                provider="ocr",
                low_confidence=True,
            )
        )
    await db.commit()
    return run, user_id, content


def request(run, **extra):
    return {
        "command_key": str(uuid.uuid4()),
        "expected_attempt_seq": 0,
        "source_sha256": run.source_sha256,
        "page": 1,
        "x": 0.1,
        "y": 0.2,
        "width": 0.6,
        "height": 0.2,
        "rotation": 0,
        "language": "eng",
        "target": "field",
        "field": "invoice_number",
        **extra,
    }


def raw(text):
    return {
        "text": text,
        "engine": "tesseract",
        "engine_version": "synthetic-test",
        "language": "eng",
        "installed_languages": ["eng", "osd"],
        "page": 1,
        "rotation": 0,
        "source_width": 600,
        "source_height": 800,
        "rotated_width": 600,
        "rotated_height": 800,
        "crop_width": 360,
        "crop_height": 160,
        "render_dpi": None,
    }


async def finish(client, db, monkeypatch, *, text="Invoice No: AFTER-2", body_extra=None):
    run, actor, content = await seed(client, db)
    body = request(run, **(body_extra or {}))
    response = await client.post(f"/api/v1/invoices/captures/{run.id}/region-reads", json=body)
    assert response.status_code == 202, response.text
    read = response.json()
    calls = []

    def recognize(original, settings):
        calls.append((original, settings))
        return raw(text)

    monkeypatch.setattr(region_ocr, "recognize", recognize)
    job = await db.scalar(
        select(Job).where(Job.org_id == run.org_id, Job.kind == capture_region.KIND)
    )
    assert await capture_region.run_region_read(db, {"region_read_id": read["id"]}, job) == {
        "status": "ready"
    }
    result = (
        await client.get(f"/api/v1/invoices/captures/{run.id}/region-reads/{read['id']}")
    ).json()
    assert calls[0][0] == content
    return run, actor, body, result, job, calls


def application(result, **extra):
    return {
        "command_key": str(uuid.uuid4()),
        "expected_review_fingerprint": result["review_fingerprint"],
        "fields": [{"field": "invoice_number", "value": "AFTER-2"}],
        **extra,
    }


@pytest.mark.asyncio
async def test_read_replay_and_lookup_commit_once_without_touching_capture(
    auth_client, db_session, monkeypatch
):
    run, actor, body, result, job, calls = await finish(auth_client, db_session, monkeypatch)
    original = run.draft_json
    replay = await auth_client.post(f"/api/v1/invoices/captures/{run.id}/region-reads", json=body)
    assert replay.status_code == 202 and replay.json()["id"] == result["id"]
    lookup = await auth_client.get(
        f"/api/v1/invoices/captures/{run.id}/region-reads/by-command/{body['command_key']}"
    )
    assert lookup.status_code == 200 and lookup.json()["id"] == result["id"]
    assert lookup.json()["result"]["fields"] == [
        {"field": "invoice_number", "line_index": None, "value": "AFTER-2"}
    ]
    await db_session.refresh(run)
    assert run.draft_json == original and run.invoice_id is None
    assert await db_session.scalar(select(func.count()).select_from(CaptureRegionRead)) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == capture_region.KIND)
        )
        == 1
    )
    assert await capture_region.run_region_read(
        db_session, {"region_read_id": result["id"]}, job
    ) == {"status": "ready"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_command_payload_conflict_is_noop(auth_client, db_session, monkeypatch):
    run, _, body, result, _, _ = await finish(auth_client, db_session, monkeypatch)
    response = await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/region-reads", json={**body, "x": 0.2}
    )
    assert response.status_code == 409 and response.json()["code"] == "region_payload_conflict"
    assert await db_session.scalar(select(func.count()).select_from(CaptureRegionRead)) == 1


@pytest.mark.asyncio
async def test_apply_and_replay_preserve_original_field_and_manual_other_fields(
    auth_client, db_session, monkeypatch
):
    run, _, _, result, _, _ = await finish(auth_client, db_session, monkeypatch)
    await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/review",
        json={"fields": [{"field": "vendor_name", "reviewed_value": "Manual Vendor"}]},
    )
    result = (
        await auth_client.get(f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}")
    ).json()
    body = application(result)
    path = f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}/apply"
    applied = await auth_client.post(path, json=body)
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied_result"]["draft"]["vendor_name"] == "Manual Vendor"
    replay = await auth_client.post(path, json=body)
    assert (
        replay.status_code == 200
        and replay.json()["applied_result"] == applied.json()["applied_result"]
    )
    field = await db_session.scalar(
        select(ExtractionField).where(
            ExtractionField.extraction_run_id == run.id, ExtractionField.field == "invoice_number"
        )
    )
    await db_session.refresh(field)
    assert field.original_value == field.value == "BEFORE-1" and field.reviewed_value == "AFTER-2"
    record = await db_session.get(CaptureRegionRead, result["id"])
    await db_session.refresh(record)
    assert json.loads(record.before_draft_json)["draft"]["invoice_number"] == "BEFORE-1"
    count = await db_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "capture.region_applied")
    )
    assert count == 1


@pytest.mark.asyncio
async def test_stale_comparison_preserves_human_correction(auth_client, db_session, monkeypatch):
    run, _, _, result, _, _ = await finish(auth_client, db_session, monkeypatch)
    await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/review",
        json={"fields": [{"field": "invoice_number", "reviewed_value": "MANUAL"}]},
    )
    response = await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}/apply",
        json=application(result),
    )
    assert response.status_code == 409 and response.json()["code"] == "region_review_changed"
    fresh = (
        await auth_client.get(f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}")
    ).json()
    assert fresh["current_draft"]["invoice_number"] == "MANUAL"


@pytest.mark.asyncio
async def test_selected_reviewed_cell_requires_explicit_overwrite(
    auth_client, db_session, monkeypatch
):
    run, _, _, result, _, _ = await finish(auth_client, db_session, monkeypatch)
    await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/review",
        json={"fields": [{"field": "invoice_number", "reviewed_value": "MANUAL"}]},
    )
    fresh = (
        await auth_client.get(f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}")
    ).json()
    path = f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}/apply"
    response = await auth_client.post(path, json=application(fresh))
    assert response.status_code == 409 and response.json()["code"] == "region_review_overwrite"
    accepted = await auth_client.post(path, json=application(fresh, overwrite_reviewed=True))
    assert accepted.status_code == 200, accepted.text


@pytest.mark.asyncio
async def test_table_maps_existing_and_appends_without_deleting_original_evidence(
    auth_client, db_session, monkeypatch
):
    run, _, _, result, _, _ = await finish(
        auth_client,
        db_session,
        monkeypatch,
        text="Repair 2 5.00 10.00\nMaterials 3 4.00 12.00",
        body_extra={"target": "table", "field": None},
    )
    assert len(result["result"]["rows"]) == 2
    selected = ["description", "quantity", "unit_price", "amount"]
    body = application(
        result,
        fields=[],
        rows=[
            {"candidate_index": 0, "target_line_index": 0, "fields": selected},
            {"candidate_index": 1, "target_line_index": None, "fields": selected},
        ],
    )
    response = await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}/apply", json=body
    )
    assert response.status_code == 200, response.text
    draft = response.json()["applied_result"]["draft"]
    assert [r["description"] for r in draft["line_items"]] == ["Repair", "Materials"]
    assert draft["line_items"][0]["amount"] == "10.00"
    newfield = await db_session.scalar(
        select(ExtractionField).where(
            ExtractionField.extraction_run_id == run.id,
            ExtractionField.line_index == 1,
            ExtractionField.field == "description",
        )
    )
    assert (
        newfield.original_value is None
        and newfield.value is None
        and newfield.reviewed_value == "Materials"
    )
    assert newfield.status == "missing"
    oldfield = await db_session.scalar(
        select(ExtractionField).where(
            ExtractionField.extraction_run_id == run.id,
            ExtractionField.line_index == 0,
            ExtractionField.field == "description",
        )
    )
    await db_session.refresh(oldfield)
    assert oldfield.original_value == "Original row" and oldfield.reviewed_value == "Repair"


@pytest.mark.asyncio
async def test_invalid_apply_is_atomic_and_noop(auth_client, db_session, monkeypatch):
    run, _, _, result, _, _ = await finish(auth_client, db_session, monkeypatch)
    response = await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}/apply",
        json=application(result, fields=[{"field": "issue_date", "value": "not-a-date"}]),
    )
    assert response.status_code == 400 and response.json()["code"] == "region_bad_value"
    read = await db_session.get(CaptureRegionRead, result["id"])
    await db_session.refresh(read)
    assert read.applied_at is None and read.before_draft_json is None


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_every_apply_side_effect(
    auth_client, db_session, monkeypatch
):
    run, _, _, result, _, _ = await finish(auth_client, db_session, monkeypatch)
    original = run.draft_json
    real = audit.record

    async def refused(db, action, **kwargs):
        if action == "capture.region_applied":
            raise RuntimeError("synthetic audit refusal")
        return await real(db, action, **kwargs)

    monkeypatch.setattr(audit, "record", refused)
    with pytest.raises(RuntimeError, match="synthetic audit refusal"):
        await auth_client.post(
            f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}/apply",
            json=application(result),
        )
    await db_session.refresh(run)
    assert run.draft_json == original
    read = await db_session.get(CaptureRegionRead, result["id"])
    await db_session.refresh(read)
    assert read.applied_at is None


@pytest.mark.asyncio
async def test_worker_fences_capture_changed_during_ocr(auth_client, db_session, monkeypatch):
    run, _, _ = await seed(auth_client, db_session)
    response = await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/region-reads", json=request(run)
    )
    read_id = response.json()["id"]
    job = await db_session.scalar(select(Job).where(Job.kind == capture_region.KIND))

    async def original(*args):
        run.attempt_seq += 1
        await db_session.commit()
        return b"original"

    monkeypatch.setattr(documents, "load_capture", original)
    monkeypatch.setattr(region_ocr, "recognize", lambda *args: raw("Invoice No: AFTER-2"))
    result = await capture_region.run_region_read(db_session, {"region_read_id": read_id}, job)
    assert result == {"status": "stale"}
    row = await db_session.get(CaptureRegionRead, read_id)
    assert row.result_json is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,field",
    [("unknown", "currency"), ("not a date", "issue_date"), ("two values 1.00 and 2.00", "amount")],
)
async def test_unclear_region_never_invents_default_values(
    auth_client, db_session, monkeypatch, text, field
):
    extra = {"field": field}
    if field == "amount":
        extra["line_index"] = 0
    _, _, _, result, _, _ = await finish(
        auth_client, db_session, monkeypatch, text=text, body_extra=extra
    )
    assert result["result"]["fields"] == []
    assert result["status"] == "ready"


@pytest.mark.asyncio
async def test_history_returns_one_effective_base_and_metadata_only_children(
    auth_client, db_session, monkeypatch
):
    run, _, _, result, _, _ = await finish(auth_client, db_session, monkeypatch)
    response = await auth_client.get(f"/api/v1/invoices/captures/{run.id}/region-reads")
    assert response.status_code == 200
    assert response.json()["current_draft"]["invoice_number"] == "BEFORE-1"
    child = response.json()["items"][0]
    assert (
        child["current_draft"] is None
        and child["reviewed_fields"] == []
        and child["result"] is None
    )


@pytest.mark.asyncio
async def test_dead_jobs_release_admission_and_show_failure_instead_of_endless_running(
    auth_client, db_session
):
    run, _, _ = await seed(auth_client, db_session)
    created = []
    for _ in range(2):
        response = await auth_client.post(
            f"/api/v1/invoices/captures/{run.id}/region-reads", json=request(run)
        )
        assert response.status_code == 202, response.text
        created.append(response.json()["id"])
    jobs = list(await db_session.scalars(select(Job).where(Job.kind == capture_region.KIND)))
    for job in jobs:
        job.status = "dead"
    await db_session.commit()
    detail = await auth_client.get(f"/api/v1/invoices/captures/{run.id}/region-reads/{created[0]}")
    assert (
        detail.json()["status"] == "failed"
        and detail.json()["error_code"] == "region_job_incomplete"
    )
    history = (await auth_client.get(f"/api/v1/invoices/captures/{run.id}/region-reads")).json()
    assert all(row["status"] == "failed" for row in history["items"])
    next_read = await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/region-reads", json=request(run)
    )
    assert next_read.status_code == 202, next_read.text


@pytest.mark.asyncio
async def test_source_hash_failure_never_calls_engine_and_returns_recovery(
    auth_client, db_session, monkeypatch
):
    from app.core import storage

    run, _, _ = await seed(auth_client, db_session)
    response = await auth_client.post(
        f"/api/v1/invoices/captures/{run.id}/region-reads", json=request(run)
    )
    read_id = response.json()["id"]
    key = storage.content_key(documents.UPLOADS, run.org_id, run.source_sha256)
    storage.get_storage().put(key, b"changed original", "image/png")

    def must_not_run(*args):
        pytest.fail("Changed source reached the native engine")

    monkeypatch.setattr(region_ocr, "recognize", must_not_run)
    job = await db_session.scalar(select(Job).where(Job.kind == capture_region.KIND))
    assert await capture_region.run_region_read(db_session, {"region_read_id": read_id}, job) == {
        "status": "failed"
    }
    read = (
        await auth_client.get(f"/api/v1/invoices/captures/{run.id}/region-reads/{read_id}")
    ).json()
    assert read["result"] is None and read["error_code"] == "stored_file_changed"
    assert "original" in read["error_detail"].lower()


@pytest.mark.asyncio
async def test_new_capture_attempt_makes_old_read_stale_on_get(
    auth_client, db_session, monkeypatch
):
    run, _, _, result, _, _ = await finish(auth_client, db_session, monkeypatch)
    run.attempt_seq = 1
    await db_session.commit()
    response = await auth_client.get(
        f"/api/v1/invoices/captures/{run.id}/region-reads/{result['id']}"
    )
    assert response.json()["status"] == "stale"
    assert response.json()["error_code"] == "region_source_changed"
