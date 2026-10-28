"""Read an immutable original region; apply only reviewed, selected proposals.

Recognition never changes the capture. Creation and application use separate
durable identities, and all review writers serialize on the capture row.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core import authz, bank_id
from app.core.identity import principal
from app.models.capture_region import CaptureRegionRead
from app.models.extraction_field import ExtractionField
from app.models.extraction_run import ExtractionRun
from app.models.job import Job
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.schemas.capture_region import (
    HEADER_FIELDS,
    LINE_FIELDS,
    RegionAppliedResult,
    RegionApplyIn,
    RegionReadIn,
    RegionReadList,
    RegionReadOut,
    RegionResult,
)
from app.schemas.invoice import InvoiceCreate, LineItemIn
from app.services import (
    audit,
    capture_failures,
    capture_memory,
    capture_pages,
    documents,
    extraction,
    filesec,
    jobs,
)

KIND = "capture.region_ocr"
MAX_HISTORY = 20
MAX_ACTIVE_ORG = 2
MAX_READS_PER_CAPTURE = 40
MAX_READS_PER_HOUR = 30
MAX_LINES = 200


class RegionError(ValueError):
    def __init__(self, detail: str, *, status: int = 409, code: str = "region_conflict"):
        super().__init__(detail)
        self.detail, self.status, self.code = detail, status, code


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _run(db: AsyncSession, org_id: str, run_id: str, *, lock=False) -> ExtractionRun:
    row = await extraction.get_capture(db, org_id, run_id, for_update=lock)
    if row is None:
        raise RegionError("Capture not found", status=404, code="capture_not_found")
    return row


async def _read(
    db: AsyncSession, org_id: str, run_id: str, read_id: str, *, lock=False
) -> CaptureRegionRead:
    query = (
        select(CaptureRegionRead)
        .where(
            CaptureRegionRead.org_id == org_id,
            CaptureRegionRead.extraction_run_id == run_id,
            CaptureRegionRead.id == read_id,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is None:
        raise RegionError("Region read not found", status=404, code="region_not_found")
    return row


def _eligible(run: ExtractionRun) -> str | None:
    if run.invoice_id is not None or run.status == "saved":
        return "This capture has already been saved to an invoice."
    if run.status != "parsed" or not run.draft_json:
        return "Wait for a parsed capture before selecting an area."
    if not run.source_sha256:
        return "The original document is unavailable."
    manifest = capture_pages.manifest(run)
    if manifest.status != "ready" or not manifest.pages:
        return "This capture has no selectable page preview."
    return None


def _assert_source(run: ExtractionRun, row: CaptureRegionRead) -> None:
    if (
        _eligible(run)
        or run.source_sha256 != row.source_sha256
        or run.attempt_seq != row.attempt_seq
    ):
        raise RegionError(
            "The capture changed or was saved. Refresh it before reading or applying another area.",
            code="region_source_changed",
        )


async def review_state(db: AsyncSession, run: ExtractionRun):
    rows = list(
        await db.scalars(
            select(ExtractionField)
            .where(
                ExtractionField.org_id == run.org_id,
                ExtractionField.extraction_run_id == run.id,
            )
            .order_by(ExtractionField.line_index.asc().nullsfirst(), ExtractionField.field.asc())
            .execution_options(populate_existing=True)
        )
    )
    try:
        envelope = json.loads(run.draft_json or "null")
        draft = json.loads(_json(envelope["draft"])) if isinstance(envelope, dict) else None
    except (ValueError, KeyError, TypeError):
        draft = None
    evidence = []
    for f in rows:
        evidence.append(
            {
                "id": f.id,
                "field": f.field,
                "line_index": f.line_index,
                "value": f.value,
                "status": f.status,
                "original_value": f.original_value,
                "normalized_value": f.normalized_value,
                "reviewed_value": f.reviewed_value,
                "provider": f.provider,
                "confidence": str(f.confidence) if f.confidence is not None else None,
            }
        )
        if draft is None or f.reviewed_value is None:
            continue
        value = f.reviewed_value
        if f.line_index is None and f.field in HEADER_FIELDS:
            draft[f.field] = value if value else None
        elif (
            f.line_index is not None
            and f.field in LINE_FIELDS
            and 0 <= f.line_index < len(draft.get("line_items", []))
        ):
            draft["line_items"][f.line_index][f.field] = (
                None if f.field.startswith("captured_") and not value else value
            )
    fingerprint = _hash(
        {
            "run": run.id,
            "attempt_seq": run.attempt_seq,
            "source": run.source_sha256,
            "status": run.status,
            "invoice_id": run.invoice_id,
            "draft_json": run.draft_json,
            "fields": evidence,
        }
    )
    reviewed = [
        {"field": f.field, "line_index": f.line_index} for f in rows if f.reviewed_value is not None
    ]
    return fingerprint, draft, reviewed, rows


async def check_review_fingerprint(db: AsyncSession, run: ExtractionRun, expected: str | None):
    """Check the caller's loaded review, requiring it after region application.

    The caller already holds the capture row lock. Legacy captures without
    applied selected-area results retain their optional version contract.
    """
    applied = await db.scalar(
        select(CaptureRegionRead.id)
        .where(
            CaptureRegionRead.org_id == run.org_id,
            CaptureRegionRead.extraction_run_id == run.id,
            CaptureRegionRead.applied_at.is_not(None),
        )
        .limit(1)
    )
    if expected is None:
        if applied is not None:
            raise RegionError(
                "This capture has saved selected-area changes. Reload its current review "
                "before saving corrections or creating an invoice.",
                code="capture_review_required",
            )
        return None
    state = await review_state(db, run)
    if expected != state[0]:
        raise RegionError(
            "The saved capture review changed. Reload its current values before saving "
            "corrections or creating an invoice.",
            code="capture_review_changed",
        )
    return state


def _visible_state(run: ExtractionRun, row: CaptureRegionRead, job_status: str | None):
    if row.applied_at is not None:
        return row.status, row.error_code, row.error_detail
    if (
        run.invoice_id is not None
        or run.status != "parsed"
        or run.source_sha256 != row.source_sha256
        or run.attempt_seq != row.attempt_seq
    ):
        return (
            "stale",
            "region_source_changed",
            "The capture changed or was saved. Start a new read from its current state.",
        )
    if row.status in ("queued", "running") and job_status in (None, "dead", "succeeded"):
        return (
            "failed",
            "region_job_incomplete",
            "The reading job stopped without a result. Review existing results, then intentionally start a new read if needed.",
        )
    return row.status, row.error_code, row.error_detail


async def output(db: AsyncSession, run: ExtractionRun, row: CaptureRegionRead) -> RegionReadOut:
    fingerprint, draft, reviewed, _ = await review_state(db, run)
    job_status = (
        await db.scalar(
            select(Job.status).where(
                Job.org_id == run.org_id,
                Job.kind == KIND,
                Job.idempotency_key == f"capture-region:{row.id}",
            )
        )
        if row.status in ("queued", "running")
        else None
    )
    visible_status, error_code, error_detail = _visible_state(run, row, job_status)
    return RegionReadOut(
        id=row.id,
        command_key=row.command_key,
        extraction_run_id=run.id,
        status=visible_status,
        request=RegionReadIn.model_validate_json(row.request_json),
        result=RegionResult.model_validate_json(row.result_json) if row.result_json else None,
        error_code=error_code,
        error_detail=error_detail,
        review_fingerprint=fingerprint,
        current_draft=draft,
        reviewed_fields=reviewed,
        applied=row.applied_at is not None,
        apply_command_key=row.apply_command_key,
        applied_result=RegionAppliedResult.model_validate_json(row.apply_result_json)
        if row.apply_result_json
        else None,
    )


async def list_reads(db: AsyncSession, org_id: str, run_id: str) -> RegionReadList:
    run = await _run(db, org_id, run_id)
    rows = list(
        await db.scalars(
            select(CaptureRegionRead)
            .options(
                defer(CaptureRegionRead.result_json),
                defer(CaptureRegionRead.apply_result_json),
                defer(CaptureRegionRead.before_draft_json),
            )
            .where(
                CaptureRegionRead.org_id == org_id,
                CaptureRegionRead.extraction_run_id == run_id,
            )
            .order_by(CaptureRegionRead.created_at.desc())
            .limit(MAX_HISTORY)
        )
    )
    fingerprint, draft, reviewed, _ = await review_state(db, run)
    job_states = (
        dict(
            (
                await db.execute(
                    select(Job.idempotency_key, Job.status).where(
                        Job.org_id == org_id,
                        Job.kind == KIND,
                        Job.idempotency_key.in_([f"capture-region:{row.id}" for row in rows]),
                    )
                )
            ).all()
        )
        if rows
        else {}
    )
    reason = _eligible(run)
    return RegionReadList(
        items=[
            RegionReadOut(
                id=row.id,
                command_key=row.command_key,
                extraction_run_id=run.id,
                status=_visible_state(run, row, job_states.get(f"capture-region:{row.id}"))[0],
                request=RegionReadIn.model_validate_json(row.request_json),
                error_code=_visible_state(run, row, job_states.get(f"capture-region:{row.id}"))[1],
                error_detail=_visible_state(run, row, job_states.get(f"capture-region:{row.id}"))[
                    2
                ],
                review_fingerprint=fingerprint,
                applied=row.applied_at is not None,
                apply_command_key=row.apply_command_key,
            )
            for row in rows
        ],
        source_sha256=run.source_sha256,
        attempt_seq=run.attempt_seq,
        review_fingerprint=fingerprint,
        current_draft=draft,
        reviewed_fields=reviewed,
        eligible=reason is None,
        reason=reason,
    )


async def get_read(db: AsyncSession, org_id: str, run_id: str, read_id: str) -> RegionReadOut:
    run = await _run(db, org_id, run_id)
    return await output(db, run, await _read(db, org_id, run_id, read_id))


async def by_command(
    db: AsyncSession, org_id: str, run_id: str, actor_id: str, command_key: str
) -> RegionReadOut:
    run = await _run(db, org_id, run_id)
    row = await db.scalar(
        select(CaptureRegionRead).where(
            CaptureRegionRead.org_id == org_id,
            CaptureRegionRead.extraction_run_id == run_id,
            CaptureRegionRead.requested_by == actor_id,
            CaptureRegionRead.command_key == command_key,
        )
    )
    if row is None:
        raise RegionError("Region read not found", status=404, code="region_not_found")
    return await output(db, run, row)


async def create(
    db: AsyncSession,
    org_id: str,
    actor_id: str,
    run_id: str,
    body: RegionReadIn,
    *,
    verify_identity: Callable[[], Awaitable[None]] | None = None,
) -> RegionReadOut:
    # Admission serializes across captures; replays are looked up before quota.
    # Serialize admissions without blocking child-FK KEY SHARE checks by a
    # capture-first review/apply/worker already holding the requested capture.
    # PostgreSQL renders key_share=True (without read=True) as NO KEY UPDATE.
    await db.scalar(
        select(Organization).where(Organization.id == org_id).with_for_update(key_share=True)
    )
    run = await _run(db, org_id, run_id, lock=True)
    if verify_identity is not None:
        await verify_identity()
        await db.refresh(run)
    elif not await _active_owner(db, org_id, actor_id):
        raise RegionError(
            "Your permissions changed. Refresh before trying again.",
            status=403,
            code="region_access_revoked",
        )
    payload = body.model_dump(mode="json")
    payload_hash = _hash({"run_id": run_id, **payload})
    existing = await db.scalar(
        select(CaptureRegionRead).where(
            CaptureRegionRead.org_id == org_id,
            CaptureRegionRead.requested_by == actor_id,
            CaptureRegionRead.command_key == str(body.command_key),
        )
    )
    if existing:
        if existing.extraction_run_id != run_id or existing.payload_hash != payload_hash:
            raise RegionError(
                "This read command was already used with different settings.",
                code="region_payload_conflict",
            )
        return await output(db, run, existing)
    reason = _eligible(run)
    if reason:
        raise RegionError(reason, code="region_not_eligible")
    if run.attempt_seq != body.expected_attempt_seq or run.source_sha256 != body.source_sha256:
        raise RegionError(
            "The source changed. Refresh the capture before selecting an area.",
            code="region_source_changed",
        )
    if body.page not in {p.number for p in capture_pages.manifest(run).pages}:
        raise RegionError(
            "Choose one of this capture's previewed pages.",
            status=400,
            code="region_page_unavailable",
        )
    _, draft, _, _ = await review_state(db, run)
    if body.line_index is not None and (
        draft is None or body.line_index >= len(draft.get("line_items", []))
    ):
        raise RegionError("The selected line no longer exists.", code="region_target_changed")
    active = await db.scalar(
        select(func.count())
        .select_from(CaptureRegionRead)
        .join(
            Job,
            (Job.org_id == CaptureRegionRead.org_id)
            & (Job.kind == KIND)
            & (Job.idempotency_key == "capture-region:" + cast(CaptureRegionRead.id, String)),
        )
        .where(
            CaptureRegionRead.org_id == org_id,
            CaptureRegionRead.status.in_(("queued", "running")),
            Job.status.in_(("queued", "running", "failed")),
        )
    )
    total = await db.scalar(
        select(func.count())
        .select_from(CaptureRegionRead)
        .where(CaptureRegionRead.org_id == org_id, CaptureRegionRead.extraction_run_id == run_id)
    )
    hourly = await db.scalar(
        select(func.count())
        .select_from(CaptureRegionRead)
        .where(
            CaptureRegionRead.org_id == org_id,
            CaptureRegionRead.created_at >= datetime.now(UTC) - timedelta(hours=1),
        )
    )
    if (
        (active or 0) >= MAX_ACTIVE_ORG
        or (total or 0) >= MAX_READS_PER_CAPTURE
        or (hourly or 0) >= MAX_READS_PER_HOUR
    ):
        raise RegionError(
            "Selected-area reading is busy or its processing allowance is reached. Review existing results before trying later.",
            status=429,
            code="region_processing_limit",
        )
    row = CaptureRegionRead(
        org_id=org_id,
        extraction_run_id=run_id,
        requested_by=actor_id,
        command_key=str(body.command_key),
        payload_hash=payload_hash,
        source_sha256=body.source_sha256,
        attempt_seq=body.expected_attempt_seq,
        request_json=_json(payload),
        status="queued",
    )
    db.add(row)
    await db.flush()
    await jobs.enqueue(
        db,
        KIND,
        {"region_read_id": row.id},
        org_id=org_id,
        idempotency_key=f"capture-region:{row.id}",
        commit=False,
    )
    await audit.record(
        db,
        "capture.region_read_requested",
        target_type="extraction_run",
        target_id=run_id,
        meta={"region_read_id": row.id, "page": body.page, "target": body.target},
    )
    await db.commit()
    return await output(db, run, row)


async def _active_owner(db: AsyncSession, org_id: str, actor_id: str | None) -> bool:
    if actor_id is None:
        return False
    user = await db.get(User, actor_id, populate_existing=True)
    org = await db.get(Organization, org_id, populate_existing=True)
    member = await db.scalar(
        select(Membership)
        .where(Membership.org_id == org_id, Membership.user_id == actor_id)
        .execution_options(populate_existing=True)
    )
    return bool(
        user
        and user.is_active
        and org
        and org.status == "active"
        and member
        and member.status == "active"
        and authz.has(principal(user, member), authz.Permission.INVOICE_WRITE)
    )


def _proposals(raw: dict, request: RegionReadIn) -> RegionResult:
    from app.services import pdf_ocr

    text = raw.get("text", "")
    if not isinstance(text, str) or len(text.encode()) > filesec.RESOURCE_BYTES["region_ocr_text"]:
        raise RegionError(
            "Selected-area output exceeded the reading limit.", code="region_output_limit"
        )
    value = None
    warnings = ["Recognition is a proposal. Verify the original before applying selected values."]
    fields = []
    rows = []
    if request.target == "table":
        items = pdf_ocr._extract_line_items(text.splitlines())
        if len(items) > 100:
            raise RegionError(
                "Select a smaller table area (at most 100 proposed rows).",
                code="region_output_limit",
            )
        rows = [item.model_dump(mode="json") for item in items]
        warnings.append(
            "Table parsing infers column roles and may default quantity, category or tax. Review every selected cell; existing lines are never removed automatically."
        )
    else:
        cleaned = " ".join(line.strip() for line in text.splitlines() if line.strip())
        field = request.field
        if field in ("vendor_name", "description", "category"):
            value = cleaned[:200] if field == "vendor_name" else cleaned[:500]
        elif field == "invoice_number":
            match = pdf_ocr._INVOICE_NO_RE.search(text)
            value = (
                match.group(1)
                if match
                else cleaned
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/._-]{0,119}", cleaned)
                else None
            )
        elif field in ("issue_date", "due_date"):
            tokens = [m.group() for m in pdf_ocr._DATE_RE.finditer(text)]
            dates = {
                v.isoformat() for token in tokens if (v := pdf_ocr._to_date(token)) is not None
            }
            value = next(iter(dates)) if len(dates) == 1 else None
        elif field == "currency":
            values = set(re.findall(r"\b[A-Z]{3}\b", cleaned)) & {
                "EUR",
                "USD",
                "GBP",
                "NOK",
                "SEK",
                "DKK",
                "CHF",
                "PLN",
                "CZK",
                "CAD",
                "AUD",
                "JPY",
            }
            value = next(iter(values)) if len(values) == 1 else None
        elif field == "supplier_iban":
            try:
                value = bank_id.optional_iban(cleaned)
            except ValueError:
                value = None
        elif field in ("po_reference", "supplier_registration_number", "supplier_vat_id"):
            value = cleaned[: 60 if field == "po_reference" else 80]
        elif field in LINE_FIELDS:
            tokens = re.findall(r"[-+]?\d[\d.,]*(?:\s*%)?", cleaned)
            if len(tokens) == 1:
                value = str(pdf_ocr._num(tokens[0].strip().rstrip("%")))
        if value:
            fields = [{"field": field, "line_index": request.line_index, "value": value}]
        elif text.strip():
            warnings.append(
                "No unambiguous field value was inferred. Inspect the raw text and select a tighter area."
            )
    return RegionResult.model_validate(
        {**raw, "fields": fields, "rows": rows, "warnings": warnings}
    )


async def run_region_read(db: AsyncSession, payload: dict, job: Job) -> dict:
    from app.services import region_ocr

    read_id = str(payload.get("region_read_id") or "")
    # Every worker uses capture -> read lock order, matching review/application.
    hint = await db.scalar(
        select(CaptureRegionRead).where(
            CaptureRegionRead.org_id == job.org_id, CaptureRegionRead.id == read_id
        )
    )
    if hint is None:
        return {"skipped": "read unavailable"}
    run = await _run(db, job.org_id, hint.extraction_run_id, lock=True)
    row = await _read(db, job.org_id, run.id, read_id, lock=True)
    if row.status in ("ready", "applied", "failed", "stale"):
        return {"status": row.status}
    if row.processing_until and _utc(row.processing_until) > datetime.now(UTC):
        raise jobs.RetryAfterError(
            "Selected-area reading is already running.", retry_after_seconds=15
        )
    try:
        _assert_source(run, row)
    except RegionError as exc:
        row.status, row.error_code, row.error_detail = "stale", exc.code, exc.detail
        await db.commit()
        return {"status": "stale"}
    if not await _active_owner(db, row.org_id, row.requested_by):
        row.status, row.error_code, row.error_detail = (
            "failed",
            "region_access_revoked",
            "The requesting user's access changed. A current planner must start a new read.",
        )
        await db.commit()
        return {"status": "failed"}
    token = str(uuid.uuid4())
    row.status, row.processing_token, row.processing_until = (
        "running",
        token,
        datetime.now(UTC) + timedelta(minutes=2),
    )
    request = RegionReadIn.model_validate_json(row.request_json)
    source_sha = row.source_sha256
    await db.commit()
    result = None
    error_code = error_detail = None
    try:
        content = await documents.load_capture(documents.UPLOADS, job.org_id, source_sha)
        if content is None:
            raise RegionError(
                "The original document is unavailable. Restore it before trying again.",
                code="region_source_unavailable",
            )
        raw = await run_in_threadpool(
            region_ocr.recognize, content, request.model_dump(mode="json")
        )
        result = _proposals(raw, request)
        if len(result.model_dump_json().encode()) > filesec.RESOURCE_BYTES["region_ocr_result"]:
            raise RegionError(
                "Selected-area output exceeded the result limit.", code="region_output_limit"
            )
    except region_ocr.RegionOcrError as exc:
        error_code, error_detail = exc.code, exc.detail
    except RegionError as exc:
        error_code, error_detail = exc.code, exc.detail
    except capture_failures.CaptureError as exc:
        error_code = exc.code
        error_detail = "The stored original could not be verified within the reading limits. Restore the original or ask an administrator to check storage before retrying."
    except Exception:  # noqa: BLE001 — native/storage failures never expose internals
        error_code, error_detail = (
            "region_read_failed",
            "The selected area could not be read. Check the original and try a smaller or clearer area.",
        )
    run = await _run(db, job.org_id, run.id, lock=True)
    row = await _read(db, job.org_id, run.id, read_id, lock=True)
    if row.processing_token != token or row.status != "running":
        return {"status": row.status}
    row.processing_token = row.processing_until = None
    try:
        _assert_source(run, row)
    except RegionError as exc:
        row.status, row.error_code, row.error_detail = "stale", exc.code, exc.detail
    else:
        if not await _active_owner(db, row.org_id, row.requested_by):
            row.status, row.error_code, row.error_detail = (
                "failed",
                "region_access_revoked",
                "Access changed while reading. This result was not published.",
            )
        elif result is not None and error_code is None:
            row.status, row.result_json = "ready", result.model_dump_json()
            row.error_code = row.error_detail = None
        else:
            row.status, row.error_code, row.error_detail = "failed", error_code, error_detail
    await audit.record(
        db,
        "capture.region_read_finished",
        target_type="extraction_run",
        target_id=run.id,
        meta={"region_read_id": row.id, "status": row.status, "error_code": row.error_code},
    )
    await db.commit()
    return {"status": row.status}


async def apply(
    db: AsyncSession,
    org_id: str,
    actor_id: str,
    run_id: str,
    read_id: str,
    body: RegionApplyIn,
    *,
    verify_identity: Callable[[], Awaitable[None]] | None = None,
) -> RegionReadOut:
    run = await _run(db, org_id, run_id, lock=True)
    row = await _read(db, org_id, run_id, read_id, lock=True)
    if verify_identity is not None:
        await verify_identity()
        await db.refresh(run)
        await db.refresh(row)
    elif not await _active_owner(db, org_id, actor_id):
        raise RegionError(
            "Your permissions changed. Refresh before applying changes.",
            status=403,
            code="region_access_revoked",
        )
    payload_hash = _hash(body.model_dump(mode="json"))
    if row.apply_command_key:
        if (
            row.apply_command_key == str(body.command_key)
            and row.apply_payload_hash == payload_hash
            and row.applied_by == actor_id
        ):
            return await output(db, run, row)
        raise RegionError(
            "This result has already been applied. Refresh the capture to review its current values.",
            code="region_already_applied",
        )
    _assert_source(run, row)
    if row.status != "ready" or not row.result_json:
        raise RegionError(
            "Wait for a successful selected-area read before applying changes.",
            code="region_result_not_ready",
        )
    fingerprint, draft, _, existing_fields = await review_state(db, run)
    if fingerprint != body.expected_review_fingerprint:
        raise RegionError(
            "The capture's values changed. Refresh comparison before applying selected changes.",
            code="region_review_changed",
        )
    if draft is None:
        raise RegionError("The editable capture is unavailable.", code="region_draft_unavailable")
    candidate = RegionResult.model_validate_json(row.result_json)
    by_key = {(f.field, f.line_index): f for f in existing_fields}
    changes: dict[tuple[str, int | None], str] = {}
    appended: list[dict] = []
    line_count = len(draft.get("line_items", []))
    for selected in body.fields:
        key = selected.field, selected.line_index
        if selected.line_index is None:
            if selected.field not in HEADER_FIELDS:
                raise RegionError(
                    "Choose a supported header field.", status=400, code="region_bad_selection"
                )
        elif selected.field not in LINE_FIELDS or selected.line_index >= line_count:
            raise RegionError(
                "Choose an existing line and supported field.",
                status=400,
                code="region_bad_selection",
            )
        if key in changes:
            raise RegionError(
                "The same field was selected more than once.",
                status=400,
                code="region_bad_selection",
            )
        changes[key] = selected.value
    used_candidates = set()
    for mapping in body.rows:
        if (
            mapping.candidate_index >= len(candidate.rows)
            or mapping.candidate_index in used_candidates
            or len(mapping.fields) != len(set(mapping.fields))
            or not set(mapping.fields) <= LINE_FIELDS
        ):
            raise RegionError(
                "The selected table mapping is invalid.", status=400, code="region_bad_selection"
            )
        used_candidates.add(mapping.candidate_index)
        proposed = candidate.rows[mapping.candidate_index]
        target = mapping.target_line_index
        if target is None:
            if not {"description", "quantity", "unit_price", "amount"} <= set(mapping.fields):
                raise RegionError(
                    "An appended row needs description, quantity, unit price and amount.",
                    status=400,
                    code="region_incomplete_row",
                )
            target = line_count + len(appended)
            new_line = {name: proposed.get(name) for name in mapping.fields}
            try:
                validated = LineItemIn.model_validate(new_line)
            except ValidationError:
                raise RegionError(
                    "The selected row contains invalid amounts or description. Review the source before applying.",
                    status=400,
                    code="region_bad_value",
                ) from None
            appended.append(validated.model_dump(mode="json"))
        elif target >= line_count:
            raise RegionError(
                "The destination line no longer exists.", code="region_target_changed"
            )
        for name in mapping.fields:
            key = name, target
            value = proposed.get(name)
            if key in changes or value is None:
                raise RegionError(
                    "The selected table cell is missing or selected twice.",
                    status=400,
                    code="region_bad_selection",
                )
            changes[key] = str(value)
    if appended and line_count + len(appended) > MAX_LINES:
        raise RegionError(
            "This capture exceeds the editable table limit. Apply fewer new rows.",
            status=400,
            code="region_line_limit",
        )
    for key, value in changes.items():
        original = by_key.get(key)
        if (
            original
            and original.reviewed_value is not None
            and value != original.reviewed_value
            and not body.overwrite_reviewed
        ):
            raise RegionError(
                "A selected cell already has a human correction. Explicitly acknowledge replacing selected corrections, or leave those cells unchecked.",
                code="region_review_overwrite",
            )
    draft["line_items"].extend(appended)
    for (field, index), value in changes.items():
        if index is None:
            draft[field] = value if value else None
        else:
            draft["line_items"][index][field] = (
                None if field.startswith("captured_") and not value else value
            )
    try:
        validated_draft = InvoiceCreate.model_validate(draft)
    except ValidationError:
        raise RegionError(
            "Selected changes would make the invoice invalid. Check dates, currency, bank details and amounts.",
            status=400,
            code="region_bad_value",
        ) from None
    before = run.draft_json
    if len((before or "").encode()) > filesec.RESOURCE_BYTES["region_ocr_output"]:
        raise RegionError(
            "The capture is too large for selected-area application. Preserve its current corrections and use manual review.",
            status=400,
            code="region_snapshot_limit",
        )
    assert before is not None
    envelope = json.loads(before)
    vendor_name = envelope["draft"].get("vendor_name")
    audit_changes = []
    learning: dict[str, tuple[str | None, str]] = {}
    for (field, index), value in changes.items():
        f = by_key.get((field, index))
        if f is None:
            f = ExtractionField(
                org_id=org_id,
                extraction_run_id=run.id,
                field=field,
                line_index=index,
                value=None,
                status="missing",
                original_value=None,
                normalized_value=None,
                provider="region-reviewed",
                confidence=None,
                low_confidence=False,
            )
            db.add(f)
            by_key[(field, index)] = f
        old = f.reviewed_value if f.reviewed_value is not None else f.value
        # Match the existing audited correction endpoint, including bank masking
        # and its explicit no-reusable-bank-instructions learning rule.
        if old != value:
            learning[field] = old, value
        f.reviewed_value, f.low_confidence = value, False
        audit_changes.append(
            {
                "field": field,
                "line_index": index,
                "old": bank_id.mask_iban(old) if field == "supplier_iban" else old,
                "new": bank_id.mask_iban(value) if field == "supplier_iban" else value,
            }
        )
    # Memory is keyed by vendor/field, not line position. Observe one final
    # accepted correction per field name in this command, avoiding duplicate
    # pending inserts for multi-row table application with autoflush disabled.
    for field, (old, value) in sorted(learning.items()):
        await capture_memory.record_correction(
            db,
            org_id,
            vendor_name=vendor_name,
            field=field,
            original_value=old,
            corrected_value=value,
        )
    # New rows expose provenance for every cell; unselected/defaulted cells
    # remain explicitly missing, not falsely machine-extracted.
    for index in range(line_count, line_count + len(appended)):
        for field in LINE_FIELDS:
            if (field, index) not in by_key:
                default_value = validated_draft.line_items[index].model_dump(mode="json").get(field)
                default_text = str(default_value) if default_value is not None else None
                f = ExtractionField(
                    org_id=org_id,
                    extraction_run_id=run.id,
                    field=field,
                    line_index=index,
                    value=default_text,
                    status="defaulted" if default_text is not None else "missing",
                    confidence=None,
                    original_value=None,
                    normalized_value=default_text,
                    reviewed_value=None,
                    provider="region-reviewed",
                    low_confidence=default_text is None,
                )
                db.add(f)
    row.before_draft_json = before
    envelope["draft"] = validated_draft.model_dump(mode="json")
    run.draft_json = _json(envelope)
    row.apply_command_key, row.apply_payload_hash, row.applied_by = (
        str(body.command_key),
        payload_hash,
        actor_id,
    )
    row.applied_at, row.status = datetime.now(UTC), "applied"
    await db.flush()
    next_fingerprint, _, _, _ = await review_state(db, run)
    row.apply_result_json = RegionAppliedResult(
        draft=validated_draft,
        review_fingerprint=next_fingerprint,
        changed_fields=len(changes),
        appended_rows=len(appended),
    ).model_dump_json()
    await audit.record(
        db,
        "capture.region_applied",
        target_type="extraction_run",
        target_id=run.id,
        meta={"region_read_id": row.id, "fields": audit_changes, "appended_rows": len(appended)},
    )
    await db.commit()
    return await output(db, run, row)
