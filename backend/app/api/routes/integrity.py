from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.integrity import DocIssueOut, IntegrityReportOut
from app.services import integrity

# Structural authorization (ADR-0024): integrity sweeps are an administrative
# operation — router-level SETTINGS_MANAGE.
router = APIRouter(
    prefix="/integrity",
    tags=["integrity"],
    dependencies=[Depends(require_perm(authz.Permission.SETTINGS_MANAGE))],
)


@router.post("/documents/verify", response_model=IntegrityReportOut)
async def verify_documents(current: CurrentUser, db: DbSession):
    """Re-hash this workspace's stored documents (receipts, logos, email
    attachments) against their recorded sha256 to detect corruption or loss.

    Synchronous for interactive use; for large tenants enqueue the
    `integrity.verify_documents` background job instead (POST /jobs)."""
    report = await integrity.verify_documents(db, current.org_id)
    return _report_out(report)


@router.post("/ledger/verify", response_model=IntegrityReportOut)
async def verify_ledger(current: CurrentUser, db: DbSession):
    """Verify the accounts-receivable ledger invariants — each issued invoice's
    amount_paid equals the sum of its payment entries, and no receipt is
    over-allocated. Admin-only; also available as the `integrity.verify_ledger`
    background job (POST /jobs)."""
    report = await integrity.verify_ledger(db, current.org_id)
    return _report_out(report)


@router.post("/versions/verify", response_model=IntegrityReportOut)
async def verify_versions(current: CurrentUser, db: DbSession):
    """Verify the document-version chain — every single-file slot (issuer logo,
    expense receipt) has exactly one current version, its sha matches the live
    pointer, and no file lacks a history. Admin-only; also available as the
    `integrity.verify_versions` background job (POST /jobs)."""
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
