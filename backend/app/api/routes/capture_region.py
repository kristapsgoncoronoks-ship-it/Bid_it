"""Selected-area OCR commands and explicit candidate application."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentUser, DbSession, refresh_current_identity, require_perm
from app.core import authz
from app.core.errors import AppError
from app.schemas.capture_region import RegionApplyIn, RegionReadIn, RegionReadList, RegionReadOut
from app.services import capture_region

router = APIRouter(
    prefix="/invoices/captures/{run_id}/region-reads",
    tags=["invoices"],
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.INVOICE_WRITE))]


def _error(exc: capture_region.RegionError):
    raise AppError(exc.detail, code=exc.code, status=exc.status) from None


@router.get("", response_model=RegionReadList)
async def list_reads(run_id: UUID, current: CurrentUser, db: DbSession):
    try:
        return await capture_region.list_reads(db, current.org_id, str(run_id))
    except capture_region.RegionError as exc:
        _error(exc)


@router.post("", response_model=RegionReadOut, status_code=202, dependencies=_WRITE)
async def create_read(
    run_id: UUID, body: RegionReadIn, request: Request, current: CurrentUser, db: DbSession
):
    async def verify():
        fresh, _ = await refresh_current_identity(request, db, current)
        authz.require(fresh, authz.Permission.INVOICE_WRITE)

    try:
        return await capture_region.create(
            db, current.org_id, current.id, str(run_id), body, verify_identity=verify
        )
    except capture_region.RegionError as exc:
        _error(exc)


@router.get("/by-command/{command_key}", response_model=RegionReadOut)
async def lookup(run_id: UUID, command_key: UUID, current: CurrentUser, db: DbSession):
    try:
        return await capture_region.by_command(
            db, current.org_id, str(run_id), current.id, str(command_key)
        )
    except capture_region.RegionError as exc:
        _error(exc)


@router.get("/{read_id}", response_model=RegionReadOut)
async def read_status(run_id: UUID, read_id: UUID, current: CurrentUser, db: DbSession):
    try:
        return await capture_region.get_read(db, current.org_id, str(run_id), str(read_id))
    except capture_region.RegionError as exc:
        _error(exc)


@router.post("/{read_id}/apply", response_model=RegionReadOut, dependencies=_WRITE)
async def apply_read(
    run_id: UUID,
    read_id: UUID,
    body: RegionApplyIn,
    request: Request,
    current: CurrentUser,
    db: DbSession,
):
    async def verify():
        fresh, _ = await refresh_current_identity(request, db, current)
        authz.require(fresh, authz.Permission.INVOICE_WRITE)

    try:
        return await capture_region.apply(
            db, current.org_id, current.id, str(run_id), str(read_id), body, verify_identity=verify
        )
    except capture_region.RegionError as exc:
        _error(exc)
