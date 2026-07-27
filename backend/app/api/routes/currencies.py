"""Tenant currency-catalog API (Slice 5a).

Read (list) is available to any authenticated user — it powers the invoice /
issuing currency pickers. Managing the catalog (create / archive) is admin-gated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.currency import Currency
from app.schemas.currency import CurrencyActivate, CurrencyCreate, CurrencyOut
from app.services import currencies, fx

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


def _out(c: Currency) -> CurrencyOut:
    """Build the response explicitly (C1.5, WO-23) rather than relying on
    Pydantic's `from_attributes` auto-conversion: `indicative` is not a column
    on `Currency` — it is derived per-request from the ONE currency registry
    (`fx.indicative_for`), so auto-conversion would silently default it to
    `None` for every row regardless of the code."""
    return CurrencyOut(
        id=c.id,
        code=c.code,
        name=c.name,
        symbol=c.symbol,
        decimal_places=c.decimal_places,
        active=c.active,
        version=c.version,
        indicative=fx.indicative_for(c.code),
    )


@router.get("", response_model=list[CurrencyOut])
async def list_currencies(current: CurrentUser, db: DbSession, include_inactive: bool = False):
    rows = await currencies.list_currencies(db, current.org_id, include_inactive=include_inactive)
    return [_out(c) for c in rows]


@router.post(
    "", response_model=CurrencyOut, status_code=status.HTTP_201_CREATED, dependencies=_ADMIN
)
async def create_currency(body: CurrencyCreate, current: CurrentUser, db: DbSession):
    try:
        cur = await currencies.create(
            db,
            current.org_id,
            code=body.code,
            name=body.name,
            symbol=body.symbol,
            decimal_places=body.decimal_places,
        )
    except currencies.CurrencyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _out(cur)


@router.patch("/{code_id}", response_model=CurrencyOut, dependencies=_ADMIN)
async def set_currency_active(
    code_id: str, body: CurrencyActivate, current: CurrentUser, db: DbSession
):
    try:
        cur = await currencies.set_active(
            db, current.org_id, code_id, active=body.active, expected_version=body.version
        )
    except currencies.ConcurrencyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except currencies.CurrencyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return _out(cur)
