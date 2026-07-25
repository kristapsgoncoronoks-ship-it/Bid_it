"""Bank reconciliation — import a statement and match its lines to receipts / paid
reimbursement payouts (Phase 12). Gated on the issuing module and the PAYMENT_*
permissions (cash application): router-level PAYMENT_READ, inline PAYMENT_WRITE on
the import + match mutations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.reconciliation import (
    BankLineOut,
    BankStatementOut,
    ImportResult,
    MatchCandidateOut,
    MatchRequest,
)
from app.services import bank_statement, filesec, modules, reconciliation

# Structural authorization (ADR-0024): viewing reconciliation needs PAYMENT_READ
# (router-level); import + match declare PAYMENT_WRITE per-route below.
router = APIRouter(
    prefix="/reconciliation",
    tags=["reconciliation"],
    dependencies=[Depends(require_perm(authz.Permission.PAYMENT_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.PAYMENT_WRITE))]


async def _guard(db: DbSession, org_id: str) -> None:
    await modules.require_enabled(db, org_id, "issuing")


async def _statement_out(db: DbSession, org_id: str, s) -> BankStatementOut:
    counts = await reconciliation.statement_counts(db, org_id, s.id)
    return BankStatementOut(
        id=s.id,
        filename=s.filename,
        source_format=s.source_format,
        method=s.method,
        line_count=s.line_count,
        created_by=s.created_by,
        created_at=s.created_at,
        **counts,
    )


async def _load_line(db: DbSession, org_id: str, line_id: str):
    line = await reconciliation.get_line(db, org_id, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bank line not found")
    return line


@router.post(
    "/import", response_model=ImportResult, status_code=status.HTTP_201_CREATED, dependencies=_WRITE
)
async def import_statement(current: CurrentUser, db: DbSession, file: UploadFile):
    await _guard(db, current.org_id)
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Statement too large (max 15 MB)"
        )
    # Security gate before any parsing/OCR of the (untrusted) statement.
    try:
        filesec.check(
            file.filename or "statement", content, allowed=frozenset({"pdf", "csv", "xml"})
        )
    except filesec.FileRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))
    try:
        result = await run_in_threadpool(
            bank_statement.parse, file.filename or "statement", content
        )
    except bank_statement.pdf_ocr.OcrUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"OCR unavailable: {exc}")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    try:
        stmt = await reconciliation.import_statement(
            db,
            current.org_id,
            filename=file.filename or "statement",
            sha=reconciliation.digest(content),
            result=result,
            created_by=current.email,
        )
    except reconciliation.ReconError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return ImportResult(
        statement_id=stmt.id,
        method=stmt.method,
        imported=stmt.line_count,
        warnings=result.warnings,
    )


@router.get("/statements", response_model=list[BankStatementOut])
async def list_statements(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    rows = await reconciliation.list_statements(db, current.org_id)
    return [await _statement_out(db, current.org_id, s) for s in rows]


@router.get("/statements/{statement_id}/lines", response_model=list[BankLineOut])
async def statement_lines(statement_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    if await reconciliation.get_statement(db, current.org_id, statement_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement not found")
    rows = await reconciliation.lines_for(db, current.org_id, statement_id)
    return [BankLineOut.model_validate(ln) for ln in rows]


@router.get("/lines/{line_id}/candidates", response_model=list[MatchCandidateOut])
async def match_candidates(line_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    line = await _load_line(db, current.org_id, line_id)
    cands = await reconciliation.suggest_matches(db, current.org_id, line)
    return [
        MatchCandidateOut(
            kind=c.kind,
            id=c.id,
            amount=c.amount,
            date=c.date,
            reference=c.reference,
            days_off=c.days_off,
            score=c.score,
        )
        for c in cands
    ]


@router.post("/lines/{line_id}/match", response_model=BankLineOut, dependencies=_WRITE)
async def match_line(line_id: str, body: MatchRequest, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    line = await _load_line(db, current.org_id, line_id)
    try:
        await reconciliation.confirm_match(db, current.org_id, line, body.kind, body.target_id)
    except reconciliation.ReconError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return BankLineOut.model_validate(line)


@router.delete("/lines/{line_id}/match", response_model=BankLineOut, dependencies=_WRITE)
async def unmatch_line(line_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    line = await _load_line(db, current.org_id, line_id)
    await reconciliation.unmatch(db, current.org_id, line)
    return BankLineOut.model_validate(line)


@router.post("/lines/{line_id}/ignore", response_model=BankLineOut, dependencies=_WRITE)
async def ignore_line(line_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    line = await _load_line(db, current.org_id, line_id)
    try:
        await reconciliation.set_ignored(db, current.org_id, line)
    except reconciliation.ReconError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return BankLineOut.model_validate(line)
