"""Tenant currency-catalog API (Slice 5a).

Read (list) is available to any authenticated user — it powers the invoice /
issuing currency pickers. Managing the catalog (create / archive) is admin-gated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.currency import CurrencyActivate, CurrencyCreate, CurrencyOut
from app.services import currencies

# Structural authorization (ADR-0024): the currency catalogue feeds pickers on
# every money form, so reading it declares INVOICE_READ — held by EVERY business
# role, i.e. behaviour-preserving "any authenticated member". Managing the
# catalogue is org configuration (SETTINGS_MANAGE, per-route).
router = APIRouter(
    prefix="/currencies",
    tags=["currencies"],
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_READ))],
)
_ADMIN = [Depends(require_perm(authz.Permission.SETTINGS_MANAGE))]


@router.get("", response_model=list[CurrencyOut])
async def list_currencies(current: CurrentUser, db: DbSession, include_inactive: bool = False):
    return await currencies.list_currencies(db, current.org_id, include_inactive=include_inactive)


@router.post(
    "", response_model=CurrencyOut, status_code=status.HTTP_201_CREATED, dependencies=_ADMIN
)
async def create_currency(body: CurrencyCreate, current: CurrentUser, db: DbSession):
    try:
        return await currencies.create(
            db,
            current.org_id,
            code=body.code,
            name=body.name,
            symbol=body.symbol,
            decimal_places=body.decimal_places,
        )
    except currencies.CurrencyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.patch("/{code_id}", response_model=CurrencyOut, dependencies=_ADMIN)
async def set_currency_active(
    code_id: str, body: CurrencyActivate, current: CurrentUser, db: DbSession
):
    try:
        return await currencies.set_active(
            db, current.org_id, code_id, active=body.active, expected_version=body.version
        )
    except currencies.ConcurrencyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except currencies.CurrencyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
