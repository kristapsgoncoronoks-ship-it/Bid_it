"""Cost-allocation master-data API (WO-14 / F1.1) — departments, cost centers,
projects.

Thin controllers over `app/services/costing.py` (Slice 1): the unique-code
guard, the status-transition rules and the optimistic-concurrency check all
live in the service; these handlers only parse, call and map errors.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
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
from app.services import costing

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
