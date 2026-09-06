from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.core.config import settings
from app.schemas.integrity import DocIssueOut, IntegrityQueuedOut, IntegrityReportOut
from app.services import integrity, job_handlers, jobs

# Structural authorization (ADR-0024): integrity sweeps are an administrative
# operation — router-level SETTINGS_MANAGE.
router = APIRouter(
    prefix="/integrity",
    tags=["integrity"],
    dependencies=[Depends(require_perm(authz.Permission.SETTINGS_MANAGE))],
)

# PERF-009 (audit 2026-09-05): each sweep used to run inside the request
# whatever the tenant's size — the document sweep re-hashes every stored
# object on the event loop, so one admin click on a large tenant held a
# worker for as long as the object store took. Now the route counts the
# references first (indexed counts, no bytes loaded): at or below
# `settings.integrity_sync_limit` it verifies and answers 200 as before;
# above it, it queues the matching background job (the worker already ran
# these sweeps for the scheduler) and answers 202 with the job, keyed per
# day so a second click reuses the queued run instead of doubling the work.
_QUEUED: dict[int | str, dict[str, Any]] = {
    202: {"model": IntegrityQueuedOut, "description": "Too large for a request; queued"}
}


@router.post("/documents/verify", response_model=IntegrityReportOut, responses=_QUEUED)
async def verify_documents(current: CurrentUser, db: DbSession):
    """Re-hash this workspace's stored documents (receipts, logos, email
    attachments, original uploads) against their recorded sha256 to detect
    corruption or loss.

    Synchronous up to `integrity_sync_limit` references; above it the
    `integrity.verify_documents` background job is queued and 202 returned —
    its result appears in the job list."""
    queued = await _queue_if_large(
        db, current.org_id, integrity.SCOPE_DOCUMENTS, job_handlers.INTEGRITY_VERIFY
    )
    if queued is not None:
        return queued
    return _report_out(await integrity.verify_documents(db, current.org_id))


@router.post("/ledger/verify", response_model=IntegrityReportOut, responses=_QUEUED)
async def verify_ledger(current: CurrentUser, db: DbSession):
    """Verify the payment-ledger invariants — each issued and received invoice's
    amount_paid equals the sum of its ledger entries, and no receipt is
    over-allocated. Admin-only; queued as the `integrity.verify_ledger` job
    (202) above `integrity_sync_limit` rows."""
    queued = await _queue_if_large(
        db, current.org_id, integrity.SCOPE_LEDGER, job_handlers.INTEGRITY_LEDGER
    )
    if queued is not None:
        return queued
    return _report_out(await integrity.verify_ledger(db, current.org_id))


@router.post("/versions/verify", response_model=IntegrityReportOut, responses=_QUEUED)
async def verify_versions(current: CurrentUser, db: DbSession):
    """Verify the document-version chain — every single-file slot (issuer logo,
    expense receipt) has exactly one current version, its sha matches the live
    pointer, and no file lacks a history. Admin-only; queued as the
    `integrity.verify_versions` job (202) above `integrity_sync_limit` rows."""
    queued = await _queue_if_large(
        db, current.org_id, integrity.SCOPE_VERSIONS, job_handlers.INTEGRITY_VERSIONS
    )
    if queued is not None:
        return queued
    return _report_out(await integrity.verify_versions(db, current.org_id))


async def _queue_if_large(db, org_id: str, scope: str, kind: str) -> JSONResponse | None:
    references = await integrity.reference_count(db, org_id, scope)
    limit = settings.integrity_sync_limit
    if references <= limit:
        return None
    day = datetime.now(UTC).date().isoformat()
    job, created = await jobs.enqueue_with_outcome(
        db, kind, {}, org_id=org_id, idempotency_key=f"{kind}:{day}"
    )
    body = IntegrityQueuedOut(
        job_id=job.id,
        job_kind=kind,
        references=references,
        sync_limit=limit,
        created=created,
    )
    return JSONResponse(status_code=202, content=body.model_dump())


def _report_out(report) -> IntegrityReportOut:
    return IntegrityReportOut(
        checked=report.checked,
        ok=report.ok,
        healthy=report.healthy,
        issues=[
            DocIssueOut(kind=i.kind, entity_id=i.entity_id, problem=i.problem, detail=i.detail)
            for i in report.issues
        ],
    )
