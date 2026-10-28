"""Real non-superuser PostgreSQL qualification for visual OCR commands."""

from __future__ import annotations

import asyncio
import io
import json
import os
import uuid
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_session
from app.core.tenant import reset_current_org, set_current_org
from app.main import app
from app.models.audit import AuditEvent
from app.models.base import Base
from app.models.extraction_field import ExtractionField
from app.models.extraction_run import ExtractionRun
from app.models.job import Job
from app.models.organization import Organization
from app.schemas.capture_pages import CapturePage, CapturePages
from app.schemas.invoice import FieldProvenance, InvoiceCreate, LineItemIn, ParsedInvoiceDraft
from app.services import documents, extraction

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")
BASE = "/api/v1/invoices/captures"
KIND = "capture.region_ocr"


@pytest_asyncio.fixture
async def region_pg(monkeypatch):
    # Existing authentication mail is outside this feature. Bootstrap must not
    # perform external SMTP when exercising an unrelated OCR workflow.
    monkeypatch.setattr(settings, "smtp_host", None)
    engine = create_async_engine(PG_URL, pool_size=8, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        assert (
            await db.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
            is False
        ), "Concurrency/RLS proof requires a nonprivileged application role"

    async def session():
        async with factory() as db:
            yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = session
    organizations: list[str] = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield factory, client, organizations
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        async with factory() as db:
            # Delete only this fixture's tenants, preserving any independent
            # tests sharing the disposable database.
            for table in reversed(Base.metadata.sorted_tables):
                if "org_id" in table.c:
                    await db.execute(table.delete().where(table.c.org_id.in_(organizations)))
            await db.execute(delete(Organization).where(Organization.id.in_(organizations)))
            await db.commit()
        await engine.dispose()


async def _identity(client, organizations):
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Overlapping region review workspace",
            "name": "Synthetic OCR Owner",
            "email": f"region-pg-{uuid.uuid4().hex}@example.com",
            "password": "Synthetic-Region-Postgres-Password-32",
        },
    )
    assert response.status_code == 201, response.text
    identity = response.json()
    organizations.append(identity["organization"]["id"])
    identity["headers"] = {"Authorization": "Bearer " + identity["token"]["access_token"]}
    return identity


async def _seed_capture(factory, identity, *, reviewed=False):
    org_id = identity["organization"]["id"]
    stream = io.BytesIO()
    with Image.new("RGB", (1200, 900), "white") as image:
        image.save(stream, format="PNG")
    content = stream.getvalue()
    draft = InvoiceCreate(
        vendor_name="Original supplier",
        invoice_number="OLD-001",
        issue_date=date(2026, 10, 1),
        currency="EUR",
        line_items=[
            LineItemIn(
                description="Original line",
                quantity="1",
                unit_price="10.00",
                amount="10.00",
                tax_rate="0",
            )
        ],
    )
    values = {
        "vendor_name": "Original supplier",
        "invoice_number": "OLD-001",
        "issue_date": "2026-10-01",
        "due_date": None,
        "currency": "EUR",
        "po_reference": None,
        "supplier_registration_number": None,
        "supplier_vat_id": None,
        "supplier_iban": None,
    }
    provenance = [
        FieldProvenance(
            field=field,
            value=value,
            original_value=value,
            normalized_value=value,
            status="extracted" if value else "missing",
            reviewed_value="Corrected supplier" if reviewed and field == "vendor_name" else None,
            provider="synthetic-original",
        )
        for field, value in values.items()
    ]
    for field, value in {
        "description": "Original line",
        "category": "uncategorized",
        "quantity": "1",
        "unit_price": "10.00",
        "amount": "10.00",
        "tax_rate": "0",
        "captured_tax_amount": None,
        "captured_gross_amount": None,
    }.items():
        provenance.append(
            FieldProvenance(
                field=field,
                line_index=0,
                value=value,
                original_value=value,
                normalized_value=value,
                status="extracted" if value else "missing",
                provider="synthetic-original",
            )
        )
    token = set_current_org(org_id)
    try:
        async with factory() as db:
            sha, _ = await documents.store(
                documents.UPLOADS,
                org_id,
                content,
                "image/png",
                db=db,
                filename="synthetic-region.png",
            )
            run = ExtractionRun(
                org_id=org_id,
                source_filename="synthetic-region.png",
                source_sha256=sha,
                method="ocr",
                status="parsed",
                attempt_seq=0,
                field_count=1,
                warning_count=0,
            )
            db.add(run)
            await db.flush()
            parsed = ParsedInvoiceDraft(
                draft=draft, method="ocr", extraction_run_id=run.id, fields=provenance
            )
            run.draft_json = parsed.model_dump_json()
            run.preview_json = CapturePages(
                status="ready",
                pages=[
                    CapturePage(
                        number=1,
                        sha256=sha,
                        size=len(content),
                        width=960,
                        height=720,
                    )
                ],
                total_pages=1,
                truncated=False,
            ).model_dump_json()
            await extraction.record_fields(db, org_id, run.id, provenance, commit=False)
            await db.commit()
            return {"id": str(run.id), "sha": sha, "org_id": org_id}
    finally:
        reset_current_org(token)


def _read_body(capture, *, key=None, target="field"):
    return {
        "command_key": key or str(uuid.uuid4()),
        "expected_attempt_seq": 0,
        "source_sha256": capture["sha"],
        "page": 1,
        "x": 0.1,
        "y": 0.1,
        "width": 0.8,
        "height": 0.6,
        "rotation": 0,
        "language": "eng",
        "target": target,
        **({"field": "invoice_number"} if target == "field" else {}),
    }


async def _post_read(client, identity, capture, body=None):
    response = await client.post(
        f"{BASE}/{capture['id']}/region-reads",
        headers=identity["headers"],
        json=body or _read_body(capture),
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _read_counts(factory, org_id):
    from app.models.capture_region import CaptureRegionRead

    token = set_current_org(org_id)
    try:
        async with factory() as db:
            return [
                await db.scalar(
                    select(func.count()).select_from(model).where(model.org_id == org_id)
                )
                for model in (CaptureRegionRead, Job, AuditEvent)
            ]
    finally:
        reset_current_org(token)


@pytest.mark.asyncio
async def test_parallel_read_command_is_one_intent_and_payload_bound(region_pg):
    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity)
    before = await _read_counts(factory, capture["org_id"])
    body = _read_body(capture)
    responses = await asyncio.gather(
        *[
            client.post(
                f"{BASE}/{capture['id']}/region-reads",
                headers=identity["headers"],
                json=body,
            )
            for _ in range(2)
        ]
    )
    assert [response.status_code for response in responses] == [202, 202], [
        response.text for response in responses
    ]
    ids = [response.json()["id"] for response in responses]
    assert ids[0] == ids[1], "Same intention created two OCR operations"
    after = await _read_counts(factory, capture["org_id"])
    assert after == [before[0] + 1, before[1] + 1, before[2] + 1]
    lookup = await client.get(
        f"{BASE}/{capture['id']}/region-reads/by-command/{body['command_key']}",
        headers=identity["headers"],
    )
    assert lookup.status_code == 200 and lookup.json()["id"] == ids[0]
    conflict = await client.post(
        f"{BASE}/{capture['id']}/region-reads",
        headers=identity["headers"],
        json={**body, "x": 0.2},
    )
    assert conflict.status_code == 409, conflict.text
    assert await _read_counts(factory, capture["org_id"]) == after


@pytest.mark.asyncio
async def test_region_rows_have_raw_rls_and_real_http_tenant_isolation(region_pg):
    factory, client, organizations = region_pg
    identities = [await _identity(client, organizations) for _ in range(2)]
    captures = [await _seed_capture(factory, identity) for identity in identities]
    reads = [
        await _post_read(client, identity, capture)
        for identity, capture in zip(identities, captures, strict=True)
    ]
    for index in range(2):
        other = 1 - index
        identity, capture = identities[index], captures[index]
        listed = await client.get(
            f"{BASE}/{capture['id']}/region-reads", headers=identity["headers"]
        )
        assert listed.status_code == 200, listed.text
        seen = {row["id"] for row in listed.json()["items"]}
        assert reads[index]["id"] in seen and reads[other]["id"] not in seen
        for run_id in (capture["id"], captures[other]["id"]):
            hidden = await client.get(
                f"{BASE}/{run_id}/region-reads/{reads[other]['id']}",
                headers=identity["headers"],
            )
            assert hidden.status_code == 404, hidden.text

        async with factory() as db:
            await db.execute(
                text("SELECT set_config('app.current_org', :org, true)"),
                {"org": capture["org_id"]},
            )
            raw = list(await db.scalars(text("SELECT id FROM capture_region_reads")))
            assert {str(uuid.UUID(str(value))) for value in raw} == {reads[index]["id"]}
            denied = await db.execute(
                text("UPDATE capture_region_reads SET status='failed' WHERE id=:id RETURNING id"),
                {"id": uuid.UUID(reads[other]["id"])},
            )
            assert denied.first() is None, "RLS let a raw update touch the rival's OCR evidence"
            with pytest.raises(DBAPIError) as error:
                await db.execute(
                    text("UPDATE capture_region_reads SET org_id=:foreign WHERE id=:id"),
                    {
                        "foreign": uuid.UUID(captures[other]["org_id"]),
                        "id": uuid.UUID(reads[index]["id"]),
                    },
                )
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
            await db.rollback()


async def _ready(factory, capture, read_id, *, table=False):
    from app.models.capture_region import CaptureRegionRead

    result = {
        "text": "Replacement service 30.00" if table else "NEW-002",
        "engine": "tesseract",
        "language": "eng",
        "installed_languages": ["eng"],
        "page": 1,
        "rotation": 0,
        "source_width": 1200,
        "source_height": 900,
        "rotated_width": 1200,
        "rotated_height": 900,
        "crop_width": 960,
        "crop_height": 540,
        "render_dpi": None,
        "warnings": [],
        "fields": [] if table else [{"field": "invoice_number", "value": "NEW-002"}],
        "rows": [
            {
                "description": "Replacement service",
                "category": "uncategorized",
                "quantity": "1",
                "unit_price": "30.00",
                "amount": "30.00",
                "tax_rate": "0",
                "captured_tax_amount": None,
                "captured_gross_amount": None,
            }
        ]
        if table
        else [],
    }
    token = set_current_org(capture["org_id"])
    try:
        async with factory() as db:
            row = await db.get(CaptureRegionRead, read_id)
            assert row is not None
            row.status = "ready"
            row.result_json = json.dumps(result)
            await db.commit()
    finally:
        reset_current_org(token)


async def _view(client, identity, capture, read_id):
    response = await client.get(
        f"{BASE}/{capture['id']}/region-reads/{read_id}", headers=identity["headers"]
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_parallel_apply_appends_once_preserves_corrections_and_replay_receipt(region_pg):
    from app.models.capture_region import CaptureRegionRead

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity, reviewed=True)
    read = await _post_read(client, identity, capture, _read_body(capture, target="table"))
    await _ready(factory, capture, read["id"], table=True)
    view = await _view(client, identity, capture, read["id"])
    before = await _read_counts(factory, capture["org_id"])
    apply_body = {
        "command_key": str(uuid.uuid4()),
        "expected_review_fingerprint": view["review_fingerprint"],
        "fields": [],
        "overwrite_reviewed": False,
        "rows": [
            {
                "candidate_index": 0,
                "target_line_index": None,
                "fields": ["description", "quantity", "unit_price", "amount"],
            }
        ],
    }
    responses = await asyncio.gather(
        *[
            client.post(
                f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
                headers=identity["headers"],
                json=apply_body,
            )
            for _ in range(2)
        ]
    )
    assert [response.status_code for response in responses] == [200, 200], [
        response.text for response in responses
    ]
    assert responses[0].json()["applied_result"] == responses[1].json()["applied_result"]
    assert responses[0].json()["applied_result"]["appended_rows"] == 1
    after = await _read_counts(factory, capture["org_id"])
    assert after == [before[0], before[1], before[2] + 1], "Apply duplicated audit/intent"
    token = set_current_org(capture["org_id"])
    try:
        async with factory() as db:
            run = await db.get(ExtractionRun, capture["id"])
            row = await db.get(CaptureRegionRead, read["id"])
            parsed = ParsedInvoiceDraft.model_validate_json(run.draft_json)
            assert len(parsed.draft.line_items) == 2
            assert parsed.draft.line_items[0].description == "Original line"
            assert parsed.draft.line_items[1].description == "Replacement service"
            assert (
                len(ParsedInvoiceDraft.model_validate_json(row.before_draft_json).draft.line_items)
                == 1
            )
            original = list(
                await db.scalars(
                    select(ExtractionField).where(ExtractionField.extraction_run_id == run.id)
                )
            )
            supplier = next(f for f in original if f.field == "vendor_name")
            assert supplier.original_value == supplier.normalized_value == "Original supplier"
            assert supplier.reviewed_value == "Corrected supplier"
            added = [f for f in original if f.line_index == 1]
            assert added
            assert all(f.original_value is None for f in added)
            chosen = {"description", "quantity", "unit_price", "amount"}
            assert all(f.normalized_value is None for f in added if f.field in chosen)
            assert all(f.status != "extracted" for f in added)
            assert all(
                f.status == "defaulted" and f.reviewed_value is None
                for f in added
                if f.field in {"category", "tax_rate"}
            )
            assert (
                next(f for f in added if f.field == "description").reviewed_value
                == "Replacement service"
            )
            assert row.applied_at is not None
    finally:
        reset_current_org(token)

    # Someone edits after the accepted command. Replaying its frozen key must
    # retain the receipt without restoring the receipt's older snapshot.
    reviewed = await client.post(
        f"{BASE}/{capture['id']}/review",
        headers=identity["headers"],
        json={
            "fields": [{"field": "vendor_name", "reviewed_value": "Later manual supplier"}],
            "expected_review_fingerprint": responses[0].json()["review_fingerprint"],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    replay = await client.post(
        f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
        headers=identity["headers"],
        json=apply_body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["applied_result"] == responses[0].json()["applied_result"]
    current = await _view(client, identity, capture, read["id"])
    assert current["current_draft"]["vendor_name"] == "Later manual supplier"
    conflict = await client.post(
        f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
        headers=identity["headers"],
        json={
            **apply_body,
            "rows": [{**apply_body["rows"][0], "fields": ["description", "amount"]}],
        },
    )
    assert conflict.status_code == 409, conflict.text


@pytest.mark.asyncio
async def test_apply_waits_for_manual_review_then_refuses_stale_fingerprint(region_pg, monkeypatch):
    from app.services import audit

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity)
    read = await _post_read(client, identity, capture)
    await _ready(factory, capture, read["id"])
    view = await _view(client, identity, capture, read["id"])
    held, release = asyncio.Event(), asyncio.Event()
    original_record = audit.record

    async def hold_manual_review(db, action, **kwargs):
        if action == audit.A.CAPTURE_REVIEW:
            held.set()
            await asyncio.wait_for(release.wait(), timeout=10)
        return await original_record(db, action, **kwargs)

    monkeypatch.setattr(audit, "record", hold_manual_review)
    manual = asyncio.create_task(
        client.post(
            f"{BASE}/{capture['id']}/review",
            headers=identity["headers"],
            json={"fields": [{"field": "vendor_name", "reviewed_value": "New manual supplier"}]},
        )
    )
    applying = None
    try:
        await asyncio.wait_for(held.wait(), timeout=10)
        applying = asyncio.create_task(
            client.post(
                f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
                headers=identity["headers"],
                json={
                    "command_key": str(uuid.uuid4()),
                    "expected_review_fingerprint": view["review_fingerprint"],
                    "fields": [{"field": "invoice_number", "value": "NEW-002"}],
                    "rows": [],
                    "overwrite_reviewed": False,
                },
            )
        )
        blocked = False
        for _ in range(60):
            async with factory() as observer:
                blocked = bool(
                    await observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE usename=current_user AND wait_event_type='Lock'"
                        )
                    )
                )
            if blocked:
                break
            await asyncio.sleep(0.025)
        assert blocked and not applying.done(), "Apply did not serialize behind manual review"
    finally:
        release.set()
        reviewed = await manual
        if applying is not None:
            result = await applying
    assert reviewed.status_code == 200, reviewed.text
    assert result.status_code == 409, result.text
    current = await _view(client, identity, capture, read["id"])
    assert current["current_draft"]["vendor_name"] == "New manual supplier"
    assert current["current_draft"]["invoice_number"] == "OLD-001"
    assert current["applied"] is False


@pytest.mark.asyncio
async def test_parallel_different_reads_enforce_org_limit_and_replay_before_quota(region_pg):
    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    captures = [await _seed_capture(factory, identity) for _ in range(3)]
    bodies = [_read_body(capture) for capture in captures]
    before = await _read_counts(factory, captures[0]["org_id"])
    responses = await asyncio.gather(
        *[
            client.post(
                f"{BASE}/{capture['id']}/region-reads",
                headers=identity["headers"],
                json=body,
            )
            for capture, body in zip(captures, bodies, strict=True)
        ]
    )
    assert sorted(response.status_code for response in responses) == [202, 202, 429], [
        response.text for response in responses
    ]
    after = await _read_counts(factory, captures[0]["org_id"])
    assert after == [before[0] + 2, before[1] + 2, before[2] + 2]
    for index, response in enumerate(responses):
        if response.status_code == 202:
            replay = await client.post(
                f"{BASE}/{captures[index]['id']}/region-reads",
                headers=identity["headers"],
                json=bodies[index],
            )
            assert replay.status_code == 202 and replay.json()["id"] == response.json()["id"]
    assert await _read_counts(factory, captures[0]["org_id"]) == after


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["membership", "attempt", "source"])
async def test_worker_commits_claim_and_rechecks_live_fences_before_result_publication(
    region_pg,
    monkeypatch,
    change,
):
    import threading

    from app.models.capture_region import CaptureRegionRead
    from app.models.membership import Membership
    from app.services import jobs, region_ocr

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity, reviewed=True)
    read = await _post_read(client, identity, capture)
    began, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def paused_recognize(content, request):
        calls.append(request)
        loop.call_soon_threadsafe(began.set)
        assert release.wait(timeout=10), "Test did not release synthetic native worker"
        return {
            "text": "NEW-002",
            "engine": "tesseract",
            "language": "eng",
            "installed_languages": ["eng"],
            "page": 1,
            "rotation": 0,
            "source_width": 1200,
            "source_height": 900,
            "rotated_width": 1200,
            "rotated_height": 900,
            "crop_width": 960,
            "crop_height": 540,
            "render_dpi": None,
        }

    monkeypatch.setattr(region_ocr, "recognize", paused_recognize)

    async def process():
        token = set_current_org(capture["org_id"])
        try:
            async with factory() as db:
                return await jobs.run_once(db, "region-pg-worker", kinds=(KIND,))
        finally:
            reset_current_org(token)

    processing = asyncio.create_task(process())
    try:
        await asyncio.wait_for(began.wait(), timeout=10)
        token = set_current_org(capture["org_id"])
        try:
            async with factory() as db:
                row = await db.get(CaptureRegionRead, read["id"])
                assert row.status == "running" and row.processing_token
                assert row.processing_until is not None and row.result_json is None
                if change == "membership":
                    member = await db.scalar(
                        select(Membership).where(
                            Membership.org_id == capture["org_id"],
                            Membership.user_id == identity["user"]["id"],
                        )
                    )
                    member.status = "removed"
                else:
                    run = await db.get(ExtractionRun, capture["id"])
                    if change == "attempt":
                        run.attempt_seq += 1
                    else:
                        run.source_sha256 = "f" * 64
                await db.commit()
        finally:
            reset_current_org(token)
    finally:
        release.set()
        processed = await processing
    assert processed.status == "succeeded"
    assert len(calls) == 1
    token = set_current_org(capture["org_id"])
    try:
        async with factory() as db:
            row = await db.get(CaptureRegionRead, read["id"])
            assert row.status == ("failed" if change == "membership" else "stale")
            assert row.result_json is None and row.applied_at is None
            supplier = await db.scalar(
                select(ExtractionField).where(
                    ExtractionField.extraction_run_id == capture["id"],
                    ExtractionField.field == "vendor_name",
                    ExtractionField.line_index.is_(None),
                )
            )
            assert supplier.original_value == "Original supplier"
            assert supplier.reviewed_value == "Corrected supplier"
    finally:
        reset_current_org(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("write", ["read", "apply"])
async def test_queued_request_revalidates_membership_after_capture_lock(region_pg, write):
    from app.models.capture_region import CaptureRegionRead
    from app.models.membership import Membership

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity)
    read = None
    if write == "apply":
        read = await _post_read(client, identity, capture)
        await _ready(factory, capture, read["id"])
        view = await _view(client, identity, capture, read["id"])
    before = await _read_counts(factory, capture["org_id"])
    async with factory() as blocker:
        await blocker.scalar(
            select(ExtractionRun).where(ExtractionRun.id == capture["id"]).with_for_update()
        )
        if write == "read":
            request = asyncio.create_task(
                client.post(
                    f"{BASE}/{capture['id']}/region-reads",
                    headers=identity["headers"],
                    json=_read_body(capture),
                )
            )
        else:
            request = asyncio.create_task(
                client.post(
                    f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
                    headers=identity["headers"],
                    json={
                        "command_key": str(uuid.uuid4()),
                        "expected_review_fingerprint": view["review_fingerprint"],
                        "fields": [{"field": "invoice_number", "value": "NEW-002"}],
                        "rows": [],
                        "overwrite_reviewed": False,
                    },
                )
            )
        try:
            blocked = False
            for _ in range(60):
                async with factory() as observer:
                    blocked = bool(
                        await observer.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE usename=current_user AND wait_event_type='Lock'"
                            )
                        )
                    )
                if blocked:
                    break
                await asyncio.sleep(0.025)
            assert blocked and not request.done()
            async with factory() as revoker:
                membership = await revoker.scalar(
                    select(Membership).where(
                        Membership.org_id == capture["org_id"],
                        Membership.user_id == identity["user"]["id"],
                    )
                )
                membership.status = "removed"
                await revoker.commit()
        finally:
            await blocker.commit()
            response = await request
    assert response.status_code == 401, response.text
    assert await _read_counts(factory, capture["org_id"]) == before
    if read is not None:
        async with factory() as db:
            row = await db.get(CaptureRegionRead, read["id"])
            assert row.status == "ready" and row.applied_at is None


@pytest.mark.asyncio
async def test_independent_capture_applications_do_not_race_shared_vendor_memory(
    region_pg,
    monkeypatch,
):
    from app.models.capture_field_memory import CaptureFieldMemory
    from app.services import capture_memory

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    captures = [await _seed_capture(factory, identity) for _ in range(2)]
    reads = [await _post_read(client, identity, capture) for capture in captures]
    for capture, read in zip(captures, reads, strict=True):
        await _ready(factory, capture, read["id"])
    views = [
        await _view(client, identity, capture, read["id"])
        for capture, read in zip(captures, reads, strict=True)
    ]
    arrived, ready = 0, asyncio.Event()
    original = capture_memory.record_correction

    async def simultaneous_memory(*args, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=10)
        return await original(*args, **kwargs)

    monkeypatch.setattr(capture_memory, "record_correction", simultaneous_memory)
    responses = await asyncio.gather(
        *[
            client.post(
                f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
                headers=identity["headers"],
                json={
                    "command_key": str(uuid.uuid4()),
                    "expected_review_fingerprint": view["review_fingerprint"],
                    "fields": [{"field": "invoice_number", "value": f"NEW-{index + 1}"}],
                    "rows": [],
                    "overwrite_reviewed": False,
                },
            )
            for index, (capture, read, view) in enumerate(zip(captures, reads, views, strict=True))
        ],
        return_exceptions=True,
    )
    assert not any(isinstance(response, BaseException) for response in responses), responses
    assert [response.status_code for response in responses] == [200, 200], [
        response.text for response in responses
    ]
    token = set_current_org(captures[0]["org_id"])
    try:
        async with factory() as db:
            memory = await db.scalar(
                select(CaptureFieldMemory).where(
                    CaptureFieldMemory.org_id == captures[0]["org_id"],
                    CaptureFieldMemory.vendor_name == "Original supplier",
                    CaptureFieldMemory.field == "invoice_number",
                )
            )
            assert memory.corrected_count == 2
    finally:
        reset_current_org(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["access", "generation", "corruption"])
async def test_worker_refuses_stale_or_corrupt_input_before_native_io(
    region_pg, monkeypatch, change
):
    from app.core import storage
    from app.models.capture_region import CaptureRegionRead
    from app.models.membership import Membership
    from app.services import jobs, region_ocr

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity, reviewed=True)
    read = await _post_read(client, identity, capture)
    called = []

    def forbidden(*args):
        called.append(args)
        raise AssertionError("A revoked or changed original reached native OCR")

    monkeypatch.setattr(region_ocr, "recognize", forbidden)
    token = set_current_org(capture["org_id"])
    try:
        async with factory() as db:
            if change == "access":
                membership = await db.scalar(
                    select(Membership).where(
                        Membership.org_id == capture["org_id"],
                        Membership.user_id == identity["user"]["id"],
                    )
                )
                membership.status = "removed"
            elif change == "generation":
                run = await db.get(ExtractionRun, capture["id"])
                run.attempt_seq += 1
            else:
                storage.get_storage().put(
                    storage.content_key(documents.UPLOADS, capture["org_id"], capture["sha"]),
                    b"damaged original bytes",
                    "image/png",
                )
            await db.commit()
        async with factory() as db:
            processed = await jobs.run_once(db, "region-input-fence-worker", kinds=(KIND,))
            assert processed.status == "succeeded"
            row = await db.get(CaptureRegionRead, read["id"])
            assert row.status == ("stale" if change == "generation" else "failed")
            assert row.result_json is None and row.applied_at is None
            supplier = await db.scalar(
                select(ExtractionField).where(
                    ExtractionField.extraction_run_id == capture["id"],
                    ExtractionField.field == "vendor_name",
                )
            )
            assert supplier.original_value == "Original supplier"
            assert supplier.reviewed_value == "Corrected supplier"
        assert called == []
    finally:
        reset_current_org(token)


@pytest.mark.asyncio
async def test_apply_audit_failure_rolls_back_receipt_fields_and_learning(region_pg, monkeypatch):
    from app.models.capture_field_memory import CaptureFieldMemory
    from app.models.capture_region import CaptureRegionRead
    from app.services import audit

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity)
    read = await _post_read(client, identity, capture)
    await _ready(factory, capture, read["id"])
    view = await _view(client, identity, capture, read["id"])
    body = {
        "command_key": str(uuid.uuid4()),
        "expected_review_fingerprint": view["review_fingerprint"],
        "fields": [{"field": "invoice_number", "value": "NEW-002"}],
        "rows": [],
        "overwrite_reviewed": False,
    }
    counts = await _read_counts(factory, capture["org_id"])
    original = audit.record

    class AuditRefusal(RuntimeError):
        pass

    async def refuse_apply(db, action, **kwargs):
        if action == "capture.region_applied":
            raise AuditRefusal("Synthetic audit refusal after application writes")
        return await original(db, action, **kwargs)

    monkeypatch.setattr(audit, "record", refuse_apply)
    with pytest.raises(AuditRefusal):
        await client.post(
            f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
            headers=identity["headers"],
            json=body,
        )
    assert await _read_counts(factory, capture["org_id"]) == counts
    token = set_current_org(capture["org_id"])
    try:
        async with factory() as db:
            row = await db.get(CaptureRegionRead, read["id"])
            assert row.status == "ready" and row.applied_at is None
            assert row.apply_command_key is row.before_draft_json is row.apply_result_json is None
            field = await db.scalar(
                select(ExtractionField).where(
                    ExtractionField.extraction_run_id == capture["id"],
                    ExtractionField.field == "invoice_number",
                )
            )
            assert field.original_value == "OLD-001" and field.reviewed_value is None
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(CaptureFieldMemory)
                    .where(
                        CaptureFieldMemory.org_id == capture["org_id"],
                    )
                )
                == 0
            )
    finally:
        reset_current_org(token)
    monkeypatch.setattr(audit, "record", original)
    accepted = await client.post(
        f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
        headers=identity["headers"],
        json=body,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["current_draft"]["invoice_number"] == "NEW-002"


@pytest.mark.asyncio
async def test_workspace_export_includes_only_own_region_evidence_and_redacts_worker_claim(
    region_pg,
):
    import zipfile

    from app.models.capture_region import CaptureRegionRead
    from app.services import workspace_export

    factory, client, organizations = region_pg
    own = await _identity(client, organizations)
    other = await _identity(client, organizations)
    captures = [await _seed_capture(factory, identity) for identity in (own, other)]
    reads = [
        await _post_read(client, identity, capture)
        for identity, capture in zip((own, other), captures, strict=True)
    ]
    planted = ["OWN-WORKER-CLAIM-PRIVATE", "OTHER-WORKER-CLAIM-PRIVATE"]
    for capture, read, claim in zip(captures, reads, planted, strict=True):
        token = set_current_org(capture["org_id"])
        try:
            async with factory() as db:
                row = await db.get(CaptureRegionRead, read["id"])
                row.processing_token = claim
                await db.commit()
        finally:
            reset_current_org(token)

    token = set_current_org(captures[0]["org_id"])
    try:
        async with factory() as db:
            path, summary = await workspace_export.build_zip_file(db, captures[0]["org_id"])
    finally:
        reset_current_org(token)
    try:
        with zipfile.ZipFile(path) as archive:
            blob = b"".join(archive.read(name) for name in archive.namelist())
            exported = [
                json.loads(line)
                for line in archive.read("data/capture_region_reads.jsonl").splitlines()
            ]
    finally:
        os.unlink(path)
    assert [row["id"] for row in exported] == [reads[0]["id"]]
    assert exported[0]["source_sha256"] == captures[0]["sha"]
    assert exported[0]["processing_token"] is None
    assert "processing_token" in summary["redacted"]["capture_region_reads"]
    assert reads[1]["id"].encode() not in blob
    assert captures[1]["id"].encode() not in blob
    for claim in planted:
        assert claim.encode() not in blob


async def _applied_header(factory, client, identity, capture):
    read = await _post_read(client, identity, capture)
    await _ready(factory, capture, read["id"])
    view = await _view(client, identity, capture, read["id"])
    applied = await client.post(
        f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
        headers=identity["headers"],
        json={
            "command_key": str(uuid.uuid4()),
            "expected_review_fingerprint": view["review_fingerprint"],
            "fields": [{"field": "invoice_number", "value": "NEW-002"}],
        },
    )
    assert applied.status_code == 200, applied.text
    return applied.json()


async def _financial_counts(factory, org_id):
    from app.models.invoice import Invoice
    from app.models.vendor import Vendor

    token = set_current_org(org_id)
    try:
        async with factory() as db:
            return [
                await db.scalar(
                    select(func.count()).select_from(model).where(model.org_id == org_id)
                )
                for model in (Vendor, Invoice)
            ]
    finally:
        reset_current_org(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["manual_review", "region_append"])
async def test_final_confirm_waits_for_review_writer_and_rejects_old_saved_draft(
    region_pg, monkeypatch, writer
):
    from app.services import audit

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity)
    before = await _applied_header(factory, client, identity, capture)
    held, release = asyncio.Event(), asyncio.Event()
    original = audit.record

    async def pause_writer(db, action, **kwargs):
        expected_action = (
            audit.A.CAPTURE_REVIEW if writer == "manual_review" else "capture.region_applied"
        )
        if action == expected_action:
            held.set()
            await asyncio.wait_for(release.wait(), timeout=10)
        return await original(db, action, **kwargs)

    if writer == "region_append":
        read = await _post_read(client, identity, capture, _read_body(capture, target="table"))
        await _ready(factory, capture, read["id"], table=True)
        write_path = f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply"
        write_body = {
            "command_key": str(uuid.uuid4()),
            "expected_review_fingerprint": before["review_fingerprint"],
            "rows": [
                {
                    "candidate_index": 0,
                    "target_line_index": None,
                    "fields": ["description", "quantity", "unit_price", "amount"],
                }
            ],
        }
    else:
        write_path = f"{BASE}/{capture['id']}/review"
        write_body = {
            "expected_review_fingerprint": before["review_fingerprint"],
            "fields": [{"field": "vendor_name", "reviewed_value": "Updated saved supplier"}],
        }
    monkeypatch.setattr(audit, "record", pause_writer)
    counts = await _financial_counts(factory, capture["org_id"])
    writing = asyncio.create_task(
        client.post(
            write_path,
            headers=identity["headers"],
            json=write_body,
        )
    )
    await asyncio.wait_for(held.wait(), timeout=10)
    confirming = asyncio.create_task(
        client.post(
            "/api/v1/invoices",
            headers=identity["headers"],
            json={
                **before["current_draft"],
                "extraction_run_id": capture["id"],
                "capture_review_fingerprint": before["review_fingerprint"],
            },
        )
    )
    completed, _ = await asyncio.wait({confirming}, timeout=0.15)
    assert not completed, "Confirm bypassed the capture lock held by a review writer"
    release.set()
    written, confirmed = await asyncio.gather(writing, confirming)
    assert written.status_code == 200, written.text
    assert confirmed.status_code == 409, confirmed.text
    assert confirmed.json()["code"] == "capture_review_changed"
    assert await _financial_counts(factory, capture["org_id"]) == counts
    state = (
        await client.get(
            f"{BASE}/{capture['id']}/region-reads",
            headers=identity["headers"],
        )
    ).json()
    if writer == "manual_review":
        assert state["current_draft"]["vendor_name"] == "Updated saved supplier"
        assert written.headers["x-capture-review-fingerprint"] == state["review_fingerprint"]
    else:
        assert len(state["current_draft"]["line_items"]) == 2


@pytest.mark.asyncio
async def test_final_confirm_wins_lock_and_later_region_apply_cannot_change_saved_capture(
    region_pg, monkeypatch
):
    from app.api.routes import invoices

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity)
    before = await _applied_header(factory, client, identity, capture)
    read = await _post_read(client, identity, capture, _read_body(capture, target="table"))
    await _ready(factory, capture, read["id"], table=True)
    held, release = asyncio.Event(), asyncio.Event()
    original = invoices._resolve_vendor

    async def pause_after_review_lock(*args, **kwargs):
        held.set()
        await asyncio.wait_for(release.wait(), timeout=10)
        return await original(*args, **kwargs)

    monkeypatch.setattr(invoices, "_resolve_vendor", pause_after_review_lock)
    confirming = asyncio.create_task(
        client.post(
            "/api/v1/invoices",
            headers=identity["headers"],
            json={
                **before["current_draft"],
                "extraction_run_id": capture["id"],
                "capture_review_fingerprint": before["review_fingerprint"],
            },
        )
    )
    await asyncio.wait_for(held.wait(), timeout=10)
    applying = asyncio.create_task(
        client.post(
            f"{BASE}/{capture['id']}/region-reads/{read['id']}/apply",
            headers=identity["headers"],
            json={
                "command_key": str(uuid.uuid4()),
                "expected_review_fingerprint": before["review_fingerprint"],
                "rows": [
                    {
                        "candidate_index": 0,
                        "target_line_index": None,
                        "fields": ["description", "quantity", "unit_price", "amount"],
                    }
                ],
            },
        )
    )
    completed, _ = await asyncio.wait({applying}, timeout=0.15)
    assert not completed, "Region apply bypassed the capture lock held by confirmation"
    release.set()
    confirmed, applied = await asyncio.gather(confirming, applying)
    assert confirmed.status_code == 201, confirmed.text
    assert applied.status_code == 409, applied.text
    assert applied.json()["code"] == "region_source_changed"
    assert await _financial_counts(factory, capture["org_id"]) == [1, 1]
    state = (
        await client.get(
            f"{BASE}/{capture['id']}/region-reads",
            headers=identity["headers"],
        )
    ).json()
    assert state["eligible"] is False
    assert len(state["current_draft"]["line_items"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["membership", "session_org"])
async def test_blocked_final_confirm_revalidates_identity_before_financial_writes(
    region_pg, change
):
    from app.models.membership import Membership
    from app.models.user import UserRole

    factory, client, organizations = region_pg
    identity = await _identity(client, organizations)
    capture = await _seed_capture(factory, identity)
    before = await _applied_header(factory, client, identity, capture)
    destination = None
    if change == "session_org":
        destination = (await _identity(client, organizations))["organization"]["id"]
        async with factory() as db:
            db.add(
                Membership(
                    org_id=destination,
                    user_id=identity["user"]["id"],
                    role=UserRole.owner,
                    status="active",
                )
            )
            await db.commit()
    counts = await _financial_counts(factory, capture["org_id"])
    work_counts = await _read_counts(factory, capture["org_id"])
    async with factory() as blocker:
        await blocker.scalar(
            select(ExtractionRun).where(ExtractionRun.id == capture["id"]).with_for_update()
        )
        confirming = asyncio.create_task(
            client.post(
                "/api/v1/invoices",
                headers=identity["headers"],
                json={
                    **before["current_draft"],
                    "extraction_run_id": capture["id"],
                    "capture_review_fingerprint": before["review_fingerprint"],
                },
            )
        )
        try:
            blocked = False
            for _ in range(60):
                async with factory() as observer:
                    blocked = bool(
                        await observer.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE usename=current_user AND wait_event_type='Lock'"
                            )
                        )
                    )
                if blocked:
                    break
                await asyncio.sleep(0.025)
            assert blocked and not confirming.done()
            if change == "membership":
                async with factory() as revoker:
                    membership = await revoker.scalar(
                        select(Membership).where(
                            Membership.org_id == capture["org_id"],
                            Membership.user_id == identity["user"]["id"],
                        )
                    )
                    membership.status = "removed"
                    await revoker.commit()
            else:
                switched = await client.post(
                    f"/api/v1/auth/switch-org/{destination}", headers=identity["headers"]
                )
                assert switched.status_code == 200, switched.text
        finally:
            await blocker.commit()
            response = await confirming
    assert response.status_code == 401, response.text
    assert await _financial_counts(factory, capture["org_id"]) == counts
    assert await _read_counts(factory, capture["org_id"]) == work_counts
    async with factory() as db:
        run = await db.get(ExtractionRun, capture["id"])
        assert run.status == "parsed" and run.invoice_id is None
