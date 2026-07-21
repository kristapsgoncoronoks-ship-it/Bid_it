from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_admin_or_above
from app.schemas.integrity import DocIssueOut, IntegrityReportOut
from app.services import integrity

router = APIRouter(prefix="/integrity", tags=["integrity"])


@router.post("/documents/verify", response_model=IntegrityReportOut)
async def verify_documents(current: CurrentUser, db: DbSession):
    """Re-hash this workspace's stored documents (receipts, logos, email
    attachments) against their recorded sha256 to detect corruption or loss.

    Synchronous for interactive use; for large tenants enqueue the
    `integrity.verify_documents` background job instead (POST /jobs)."""
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can run integrity checks")
    report = await integrity.verify_documents(db, current.org_id)
    return _report_out(report)


@router.post("/ledger/verify", response_model=IntegrityReportOut)
async def verify_ledger(current: CurrentUser, db: DbSession):
    """Verify the accounts-receivable ledger invariants — each issued invoice's
    amount_paid equals the sum of its payment entries, and no receipt is
    over-allocated. Admin-only; also available as the `integrity.verify_ledger`
    background job (POST /jobs)."""
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can run integrity checks")
    report = await integrity.verify_ledger(db, current.org_id)
    return _report_out(report)


@router.post("/versions/verify", response_model=IntegrityReportOut)
async def verify_versions(current: CurrentUser, db: DbSession):
    """Verify the document-version chain — every single-file slot (issuer logo,
    expense receipt) has exactly one current version, its sha matches the live
    pointer, and no file lacks a history. Admin-only; also available as the
    `integrity.verify_versions` background job (POST /jobs)."""
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can run integrity checks")
    report = await integrity.verify_versions(db, current.org_id)
    return _report_out(report)


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
