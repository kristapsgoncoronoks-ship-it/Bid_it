"""Extraction-lineage service (Slice 5b).

Records one row per capture attempt at the parse choke point and links it to the
invoice when the reviewed draft is saved. Tenant-scoped by the caller's `org_id`.
"""

from __future__ import annotations

import hashlib

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction_field import ExtractionField
from app.models.extraction_run import ExtractionRun

# Job kind for async direct-upload OCR (Stage B). Defined here (the capture
# service) so both the enqueuing route and the worker handler import it without a
# circular dependency on job_handlers.
UPLOAD_EXTRACT_KIND = "upload.extract"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def record_fields(db: AsyncSession, org_id: str, run_id: str, fields) -> None:
    """Persist per-field provenance for a run (Slice 5f). `fields` are the parser's
    FieldProvenance items. Commits (the run already exists)."""
    for f in fields:
        db.add(
            ExtractionField(
                org_id=org_id,
                extraction_run_id=run_id,
                field=f.field,
                value=(f.value[:500] if f.value else None),
                status=f.status,
                confidence=f.confidence,
            )
        )
    if fields:
        await db.commit()


async def fields_for_run(db: AsyncSession, org_id: str, run_id: str) -> list[ExtractionField]:
    return list(
        await db.scalars(
            select(ExtractionField)
            .where(ExtractionField.org_id == org_id, ExtractionField.extraction_run_id == run_id)
            .order_by(ExtractionField.field.asc())
        )
    )


async def record(
    db: AsyncSession,
    org_id: str,
    *,
    filename: str | None,
    sha256: str | None,
    method: str,
    status: str,
    field_count: int = 0,
    warning_count: int = 0,
    note: str | None = None,
) -> ExtractionRun:
    """Persist a capture attempt (no invoice yet). Commits."""
    run = ExtractionRun(
        org_id=org_id,
        source_filename=filename,
        source_sha256=sha256,
        method=method,
        status=status,
        field_count=field_count,
        warning_count=warning_count,
        note=(note[:2000] if note else None),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def link_to_invoice(db: AsyncSession, org_id: str, run_id: str, invoice_id: str) -> bool:
    """Attach a still-unlinked capture run to the saved invoice (status→saved).
    Tenant-scoped; only an unlinked run in this org is touched. Does NOT commit —
    the caller commits with the invoice save. Returns whether a row was linked."""
    result = await db.execute(
        update(ExtractionRun)
        .where(
            ExtractionRun.id == run_id,
            ExtractionRun.org_id == org_id,
            ExtractionRun.invoice_id.is_(None),
        )
        .values(invoice_id=invoice_id, status="saved")
    )
    return result.rowcount > 0


async def list_for_invoice(db: AsyncSession, org_id: str, invoice_id: str) -> list[ExtractionRun]:
    return list(
        await db.scalars(
            select(ExtractionRun)
            .where(ExtractionRun.org_id == org_id, ExtractionRun.invoice_id == invoice_id)
            .order_by(ExtractionRun.created_at.asc())
        )
    )


# --------------------------------------------------------------------------- #
# Async direct-upload capture (Stage B): the parse/OCR runs on the WORKER tier,
# not in the web request. The route stores the bytes + creates a QUEUED run and
# enqueues UPLOAD_EXTRACT_KIND; `extract_upload` (below) parses off-tier and
# stores the draft; the client polls `get_capture` for the result.
# --------------------------------------------------------------------------- #


async def start_capture(
    db: AsyncSession, org_id: str, *, filename: str | None, sha256: str
) -> ExtractionRun:
    """Create a QUEUED capture run for an accepted upload (no parse yet). Commits."""
    run = ExtractionRun(
        org_id=org_id,
        source_filename=(filename[:255] if filename else None),
        source_sha256=sha256,
        method="pending",
        status="queued",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def get_capture(db: AsyncSession, org_id: str, run_id: str) -> ExtractionRun | None:
    return await db.scalar(
        select(ExtractionRun).where(ExtractionRun.id == run_id, ExtractionRun.org_id == org_id)
    )


async def extract_upload(db: AsyncSession, run_id: str) -> dict:
    """Worker-side parse of one queued UI upload (idempotent). Loads the stored
    bytes, runs the deterministic-first parser (OCR fallback) OFF the API tier,
    and stores the draft on the run (`parsed`) or the reason (`failed`). Runs in
    the job's tenant scope. Mirrors `email_intake.extract_inbound`."""
    from app.services import documents
    from app.services.parser import parse_invoice_file

    run = await db.scalar(select(ExtractionRun).where(ExtractionRun.id == run_id))
    if run is None:
        return {"skipped": "run gone"}
    if run.status not in ("queued", "failed"):
        return {"skipped": f"status={run.status}"}  # already parsed/saved

    content = await documents.load(documents.UPLOADS, run.org_id, run.source_sha256)
    if content is None:
        run.method = "failed"
        run.status = "failed"
        run.note = "stored upload missing"
        await db.commit()
        return {"status": "failed", "reason": "missing bytes"}

    try:
        draft = await run_in_threadpool(
            parse_invoice_file, run.source_filename or "upload", content
        )
    except ValueError as exc:
        run.method = "failed"
        run.status = "failed"
        run.note = str(exc)[:2000]
        await db.commit()
        return {"status": "failed"}

    draft.draft.extraction_run_id = run.id
    draft.extraction_run_id = run.id
    run.method = draft.method
    run.status = "parsed"
    run.field_count = len(draft.draft.line_items)
    run.warning_count = len(draft.warnings)
    run.note = draft.warnings[0] if draft.warnings else None
    run.draft_json = draft.model_dump_json()
    await db.commit()
    # Per-field provenance (Slice 5f), now recorded on the worker tier.
    await record_fields(db, run.org_id, run.id, draft.fields)
    return {"status": "parsed", "method": draft.method}
