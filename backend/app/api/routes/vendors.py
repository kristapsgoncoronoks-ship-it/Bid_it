"""Vendor routes (WO-2) — thin controllers over `services.vendors`.

Structural authorization (ADR-0024): the vendor master feeds pickers/filters —
router-level INVOICE_READ. Creating/updating a vendor sets the tax id and the
IBAN that payment runs pay out to, so the mutations declare INVOICE_WRITE.
Approving/rejecting a protected-field change is an org-administration control
(SETTINGS_MANAGE) *plus* the in-service maker≠checker rule — the permission
alone is deliberately not sufficient (invariant §4.8).

RESPONSE-SHAPE DECISION (WO-2 step 4): PATCH always returns **200 with the
vendor as stored** — a protected-field change appears ONLY under
`pending_changes` (status "pending"), never in the vendor fields. See
`schemas.vendor.VendorOut` for the rationale (a PATCH may mix protected and
non-protected fields; the caller needs the resulting row either way).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.vendor import Vendor
from app.models.vendor_change_request import CR_STATUSES, VendorChangeRequest
from app.schemas.vendor import (
    ChangeDecisionIn,
    VendorChangeRequestOut,
    VendorCreate,
    VendorOut,
    VendorUpdate,
)
from app.services import vendors as vendor_service

router = APIRouter(
    prefix="/vendors",
    tags=["vendors"],
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.INVOICE_WRITE))]
_APPROVE = [Depends(require_perm(authz.Permission.SETTINGS_MANAGE))]


def _request_out(
    req: VendorChangeRequest, vendor_name: str | None = None
) -> VendorChangeRequestOut:
    out = VendorChangeRequestOut.model_validate(req)
    out.vendor_name = vendor_name
    return out


def _vendor_out(vendor: Vendor, pending: list[VendorChangeRequest]) -> VendorOut:
    out = VendorOut.model_validate(vendor)
    out.pending_changes = [_request_out(r, vendor.name) for r in pending]
    return out


@router.get("", response_model=list[VendorOut])
async def list_vendors(current: CurrentUser, db: DbSession) -> list[VendorOut]:
    rows = await vendor_service.list_vendors(db, current.org_id)
    pending = await vendor_service.pending_requests_for(db, current.org_id, [v.id for v in rows])
    return [_vendor_out(v, pending.get(v.id, [])) for v in rows]


@router.post("", response_model=VendorOut, status_code=status.HTTP_201_CREATED, dependencies=_WRITE)
async def create_vendor(body: VendorCreate, current: CurrentUser, db: DbSession) -> VendorOut:
    vendor = await vendor_service.create_vendor(db, current.org_id, body)
    await db.commit()
    await db.refresh(vendor)
    return _vendor_out(vendor, [])


# NOTE: the /changes routes are declared BEFORE /{vendor_id} so the literal
# path segment wins route matching.


@router.get("/changes", response_model=list[VendorChangeRequestOut])
async def list_change_requests(
    current: CurrentUser, db: DbSession, status_filter: str | None = "pending"
) -> list[VendorChangeRequestOut]:
    """Protected-field change requests (default: pending — the approval inbox).
    Pass `status_filter=all` for the full history."""
    wanted = None if status_filter in (None, "", "all") else status_filter
    if wanted is not None and wanted not in CR_STATUSES:
        wanted = "pending"
    rows = await vendor_service.list_change_requests(db, current.org_id, status=wanted)
    return [_request_out(req, name) for req, name in rows]


@router.post(
    "/changes/{request_id}/approve",
    response_model=VendorChangeRequestOut,
    dependencies=_APPROVE,
)
async def approve_change(
    request_id: str, body: ChangeDecisionIn, current: CurrentUser, db: DbSession
) -> VendorChangeRequestOut:
    req, vendor = await vendor_service.approve_change(
        db,
        current.org_id,
        request_id,
        approver_id=current.id,
        approver_email=current.email,
        note=body.note,
    )
    await db.commit()
    await db.refresh(req)
    return _request_out(req, vendor.name)


@router.post(
    "/changes/{request_id}/reject",
    response_model=VendorChangeRequestOut,
    dependencies=_APPROVE,
)
async def reject_change(
    request_id: str, body: ChangeDecisionIn, current: CurrentUser, db: DbSession
) -> VendorChangeRequestOut:
    req = await vendor_service.reject_change(
        db,
        current.org_id,
        request_id,
        approver_id=current.id,
        approver_email=current.email,
        note=body.note,
    )
    await db.commit()
    await db.refresh(req)
    return _request_out(req)


@router.patch("/{vendor_id}", response_model=VendorOut, dependencies=_WRITE)
async def update_vendor(
    vendor_id: str, body: VendorUpdate, current: CurrentUser, db: DbSession
) -> VendorOut:
    vendor, _created = await vendor_service.update_vendor(
        db,
        current.org_id,
        vendor_id,
        body,
        actor_id=current.id,
        actor_email=current.email,
    )
    await db.commit()
    await db.refresh(vendor)
    pending = await vendor_service.pending_requests_for(db, current.org_id, [vendor.id])
    return _vendor_out(vendor, pending.get(vendor.id, []))
