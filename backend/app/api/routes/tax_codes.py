"""Tenant tax-code catalog API (Slice 4a).

Read (list) is available to any authenticated user — it powers the issuing UI's
VAT-rate picker. Managing the catalog (create / archive) is admin-gated.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.schemas.tax_code import TaxCodeActivate, TaxCodeCreate, TaxCodeOut
from app.services import tax_codes

router = APIRouter(prefix="/tax-codes", tags=["tax-codes"])


def _require_admin(current: CurrentUser) -> None:
    authz.require(current, authz.Permission.SETTINGS_MANAGE)


@router.get("", response_model=list[TaxCodeOut])
async def list_tax_codes(
    current: CurrentUser,
    db: DbSession,
    country: str | None = None,
    include_inactive: bool = False,
):
    return await tax_codes.list_codes(
        db, current.org_id, country=country, include_inactive=include_inactive
    )


@router.post("", response_model=TaxCodeOut, status_code=status.HTTP_201_CREATED)
async def create_tax_code(body: TaxCodeCreate, current: CurrentUser, db: DbSession):
    _require_admin(current)
    try:
        return await tax_codes.create(
            db,
            current.org_id,
            code=body.code,
            name=body.name,
            rate=body.rate,
            category=body.category,
            country=body.country,
        )
    except tax_codes.TaxCodeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.patch("/{code_id}", response_model=TaxCodeOut)
async def set_tax_code_active(
    code_id: str, body: TaxCodeActivate, current: CurrentUser, db: DbSession
):
    _require_admin(current)
    try:
        return await tax_codes.set_active(
            db, current.org_id, code_id, active=body.active, expected_version=body.version
        )
    except tax_codes.ConcurrencyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except tax_codes.TaxCodeError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
