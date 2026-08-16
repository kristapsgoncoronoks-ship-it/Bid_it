"""Cost-allocation master-data API (WO-14 / F1.1) — departments, cost centers,
projects.

Thin controllers over `app/services/costing.py` (Slice 1): the unique-code
guard, the status-transition rules and the optimistic-concurrency check all
live in the service; these handlers only parse, call and map errors.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.core.security_headers import content_disposition
from app.models.costing import CostCenter, Department, Project
from app.schemas.costing import (
    CostCenterCreate,
    CostCenterOut,
    DepartmentOut,
    MasterCreate,
    MasterUpdate,
    ProjectCreate,
    ProjectOut,
)
from app.schemas.project_profit import CostEntryIn, CostEntryOut, ProjectDocumentOut, ProjectPnlOut
from app.services import audit, costing, project_profit

# Structural authorization (ADR-0024): the masters feed the cost-allocation
# pickers on invoice and expense forms, so reading them declares INVOICE_READ —
# held by EVERY business role (same rationale as the tax-code and currency
# catalogues). Managing them is org configuration (SETTINGS_MANAGE, per-route).
router = APIRouter(
    prefix="/masters",
    tags=["masters"],
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_READ))],
)
_ADMIN = [Depends(require_perm(authz.Permission.SETTINGS_MANAGE))]


def _raise(exc: costing.CostingError) -> NoReturn:
    """Map service errors to the wire: stale write / rule violation → 409;
    a missing (or other tenant's — indistinguishable, §4.4) id → opaque 404."""
    if isinstance(exc, costing.NotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    raise HTTPException(status.HTTP_409_CONFLICT, str(exc))


# --- Departments -----------------------------------------------------------


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(current: CurrentUser, db: DbSession, include_archived: bool = False):
    return await costing.list_entities(
        db, Department, current.org_id, include_archived=include_archived
    )


@router.post(
    "/departments",
    response_model=DepartmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_ADMIN,
)
async def create_department(body: MasterCreate, current: CurrentUser, db: DbSession):
    try:
        return await costing.create_department(db, current.org_id, code=body.code, name=body.name)
    except costing.CostingError as exc:
        _raise(exc)


@router.patch("/departments/{entity_id}", response_model=DepartmentOut, dependencies=_ADMIN)
async def update_department(
    entity_id: str, body: MasterUpdate, current: CurrentUser, db: DbSession
):
    try:
        return await costing.update(
            db,
            Department,
            current.org_id,
            entity_id,
            expected_version=body.version,
            name=body.name,
            status=body.status,
        )
    except costing.CostingError as exc:
        _raise(exc)


# --- Cost centers ----------------------------------------------------------


@router.get("/cost-centers", response_model=list[CostCenterOut])
async def list_cost_centers(current: CurrentUser, db: DbSession, include_archived: bool = False):
    return await costing.list_entities(
        db, CostCenter, current.org_id, include_archived=include_archived
    )


@router.post(
    "/cost-centers",
    response_model=CostCenterOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_ADMIN,
)
async def create_cost_center(body: CostCenterCreate, current: CurrentUser, db: DbSession):
    try:
        return await costing.create_cost_center(
            db, current.org_id, code=body.code, name=body.name, department_id=body.department_id
        )
    except costing.CostingError as exc:
        _raise(exc)


@router.patch("/cost-centers/{entity_id}", response_model=CostCenterOut, dependencies=_ADMIN)
async def update_cost_center(
    entity_id: str, body: MasterUpdate, current: CurrentUser, db: DbSession
):
    try:
        return await costing.update(
            db,
            CostCenter,
            current.org_id,
            entity_id,
            expected_version=body.version,
            name=body.name,
            status=body.status,
        )
    except costing.CostingError as exc:
        _raise(exc)


# --- Projects --------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(current: CurrentUser, db: DbSession, include_archived: bool = False):
    return await costing.list_entities(
        db, Project, current.org_id, include_archived=include_archived
    )


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_ADMIN,
)
async def create_project(body: ProjectCreate, current: CurrentUser, db: DbSession):
    try:
        return await costing.create_project(
            db,
            current.org_id,
            code=body.code,
            name=body.name,
            start_date=body.start_date,
            end_date=body.end_date,
        )
    except costing.CostingError as exc:
        _raise(exc)


@router.patch("/projects/{entity_id}", response_model=ProjectOut, dependencies=_ADMIN)
async def update_project(entity_id: str, body: MasterUpdate, current: CurrentUser, db: DbSession):
    try:
        return await costing.update(
            db,
            Project,
            current.org_id,
            entity_id,
            expected_version=body.version,
            name=body.name,
            status=body.status,
        )
    except costing.CostingError as exc:
        _raise(exc)


# --- Project profitability (phase 1, docs/design/project-profitability.md) ---
#
# Subresources of the project master rather than a second router: the project
# IS the master row, and the pickers/screens already know this prefix. Reads
# ride the router-level INVOICE_READ; booking costs and attaching contracts is
# bookkeeping, not org configuration, so mutations declare INVOICE_WRITE
# (NOT the masters' SETTINGS_MANAGE — a finance user who cannot edit org
# settings can still book a wage line onto a job).

_BOOKKEEPING = [Depends(require_perm(authz.Permission.INVOICE_WRITE))]


@router.get("/projects-pnl-summary", response_model=list[ProjectPnlOut])
async def projects_pnl_summary(current: CurrentUser, db: DbSession):
    """Every project's headline figures — the list screen's question is "which
    contracts lose money", so profit and margin ride with code/name/status."""
    return [ProjectPnlOut(**row) for row in await project_profit.pnl_summary(db, current.org_id)]


@router.get("/projects/{entity_id}/pnl", response_model=ProjectPnlOut)
async def project_pnl(entity_id: str, current: CurrentUser, db: DbSession):
    """The LIVE project P&L (revenue − costs, NET EUR — the response's `basis`
    states it). Phase 2 adds the freeze at close."""
    try:
        return ProjectPnlOut(**await project_profit.pnl(db, current.org_id, entity_id))
    except project_profit.ProjectProfitError as exc:
        _raise_pp(exc)


def _raise_pp(exc: project_profit.ProjectProfitError) -> NoReturn:
    if isinstance(exc, project_profit.NotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


def _entry_out(e) -> CostEntryOut:
    return CostEntryOut(
        id=e.id,
        label=e.label,
        category=e.category,
        amount=str(e.amount),
        currency=e.currency,
        entry_date=e.entry_date.isoformat() if e.entry_date else None,
        note=e.note,
        created_by=e.created_by,
        created_at=e.created_at.isoformat(),
    )


@router.get("/projects/{entity_id}/cost-entries", response_model=list[CostEntryOut])
async def list_cost_entries(entity_id: str, current: CurrentUser, db: DbSession):
    try:
        rows = await project_profit.list_cost_entries(db, current.org_id, entity_id)
    except project_profit.ProjectProfitError as exc:
        _raise_pp(exc)
    return [_entry_out(e) for e in rows]


@router.post(
    "/projects/{entity_id}/cost-entries",
    response_model=CostEntryOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_BOOKKEEPING,
)
async def add_cost_entry(entity_id: str, body: CostEntryIn, current: CurrentUser, db: DbSession):
    try:
        entry = await project_profit.add_cost_entry(
            db,
            current.org_id,
            entity_id,
            label=body.label,
            category=body.category,
            amount=body.amount,
            entry_date=body.entry_date,
            note=body.note,
            created_by=current.email,
        )
    except project_profit.ProjectProfitError as exc:
        _raise_pp(exc)
    await audit.record(
        db,
        "project.cost_entry_add",
        target_type="project",
        target_id=entity_id,
        meta={"label": entry.label, "category": entry.category, "amount": str(entry.amount)},
    )
    await db.commit()
    await db.refresh(entry)
    return _entry_out(entry)


@router.delete(
    "/projects/{entity_id}/cost-entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_BOOKKEEPING,
)
async def delete_cost_entry(entity_id: str, entry_id: str, current: CurrentUser, db: DbSession):
    try:
        entry = await project_profit.delete_cost_entry(db, current.org_id, entity_id, entry_id)
    except project_profit.ProjectProfitError as exc:
        _raise_pp(exc)
    # The audit meta carries WHAT was removed: after the commit the event is the
    # only trace the line existed, and a P&L that changed needs an explanation.
    await audit.record(
        db,
        "project.cost_entry_delete",
        target_type="project",
        target_id=entity_id,
        meta={"label": entry.label, "category": entry.category, "amount": str(entry.amount)},
    )
    await db.commit()


def _doc_out(d) -> ProjectDocumentOut:
    return ProjectDocumentOut(
        id=d.id,
        kind=d.kind,
        filename=d.filename,
        content_type=d.content_type,
        uploaded_by=d.uploaded_by,
        created_at=d.created_at.isoformat(),
    )


@router.get("/projects/{entity_id}/documents", response_model=list[ProjectDocumentOut])
async def list_project_documents(entity_id: str, current: CurrentUser, db: DbSession):
    try:
        rows = await project_profit.list_documents(db, current.org_id, entity_id)
    except project_profit.ProjectProfitError as exc:
        _raise_pp(exc)
    return [_doc_out(d) for d in rows]


@router.post(
    "/projects/{entity_id}/documents",
    response_model=ProjectDocumentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_BOOKKEEPING,
)
async def attach_project_document(
    entity_id: str,
    current: CurrentUser,
    db: DbSession,
    file: UploadFile,
    kind: str = "contract",
):
    data = await file.read()
    try:
        row = await project_profit.attach_document(
            db,
            current.org_id,
            entity_id,
            data=data,
            filename=file.filename or "document",
            content_type=file.content_type,
            kind=kind,
            uploaded_by=current.email,
        )
    except project_profit.ProjectProfitError as exc:
        _raise_pp(exc)
    await audit.record(
        db,
        "project.document_attach",
        target_type="project",
        target_id=entity_id,
        meta={"kind": row.kind, "filename": row.filename, "sha256": row.sha256},
    )
    await db.commit()
    await db.refresh(row)
    return _doc_out(row)


@router.get("/projects/{entity_id}/documents/{document_id}/download")
async def download_project_document(
    entity_id: str, document_id: str, current: CurrentUser, db: DbSession
):
    """Served inert (attachment + nosniff), like every document route: an
    uploaded contract is still attacker-influenced bytes."""
    try:
        row, data = await project_profit.load_document(db, current.org_id, entity_id, document_id)
    except project_profit.ProjectProfitError as exc:
        _raise_pp(exc)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(row.filename, fallback="attachment"),
            "X-Content-Type-Options": "nosniff",
        },
    )
