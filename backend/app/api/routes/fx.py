from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.fx import EcbRate
from app.core.roles import is_admin_or_above
from app.schemas.fx import (
    ConvertResponse,
    FxComparison,
    RateOut,
    RatesResponse,
    RefreshResult,
)
from app.services import fx

router = APIRouter(prefix="/fx", tags=["fx"])
_CENTS = Decimal("0.01")


@router.get("/rates", response_model=RatesResponse)
async def get_rates(
    current: CurrentUser,
    db: DbSession,
    on: date | None = Query(default=None, description="as-of date (default today)"),
):
    as_of = on or date.today()
    currencies = list(await db.scalars(select(EcbRate.currency).distinct().order_by(EcbRate.currency)))
    rates: list[RateOut] = []
    latest_used: date | None = None
    for ccy in currencies:
        r = await fx.resolve_rate(db, ccy, as_of)
        if r is None:
            continue
        rates.append(RateOut(currency=ccy, rate=r.rate, rate_date=r.rate_date, approximate=r.approximate))
        if latest_used is None or r.rate_date > latest_used:
            latest_used = r.rate_date
    return RatesResponse(base="EUR", as_of=latest_used, rates=rates)


@router.get("/convert", response_model=ConvertResponse)
async def convert(
    current: CurrentUser,
    db: DbSession,
    amount: Decimal = Query(ge=0),
    from_currency: str = Query(alias="from", min_length=3, max_length=3),
    to_currency: str = Query(default="EUR", alias="to", min_length=3, max_length=3),
    on: date | None = None,
):
    as_of = on or date.today()
    frm = from_currency.upper()
    to = to_currency.upper()
    r_from = await fx.resolve_rate(db, frm, as_of)
    r_to = await fx.resolve_rate(db, to, as_of)
    if r_from is None or r_to is None:
        missing = frm if r_from is None else to
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No ECB rate available for {missing}")

    eur = amount / r_from.rate
    converted = (eur * r_to.rate).quantize(_CENTS, rounding=ROUND_HALF_UP)
    eff_rate = (r_to.rate / r_from.rate).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    used_date = min(r_from.rate_date, r_to.rate_date)
    return ConvertResponse(
        amount=amount,
        from_currency=frm,
        to_currency=to,
        converted=converted,
        rate=eff_rate,
        rate_date=used_date,
        approximate=r_from.approximate or r_to.approximate,
    )


@router.get("/ecb-comparison", response_model=FxComparison)
async def ecb_comparison(
    current: CurrentUser, db: DbSession, start: date | None = None, end: date | None = None
):
    """Foreign-currency invoices valued at ECB, and — where the invoice states its
    own FX rate — the EUR markup paid versus the ECB reference rate."""
    return await fx.ecb_comparison(db, current.org_id, start, end)


@router.post("/refresh", response_model=RefreshResult)
async def refresh(current: CurrentUser, db: DbSession, history: bool = True):
    """Pull the latest ECB reference rates into the cache (owner only)."""
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can refresh rates")
    return await fx.refresh_from_ecb(db, history=history)
