from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.core.dimensions import DIMENSION_KEYS, DIMENSIONS, is_dimension
from app.core.security_headers import content_disposition
from app.schemas.analytics import (
    ByCategoryOut,
    ByStatusOut,
    DimensionBreakdown,
    SpendOverTimeOut,
    SummaryOut,
    TopVendorsOut,
)
from app.schemas.ap_aging import ApAgingOut, WorklistItemOut
from app.schemas.benchmark import CombinedBenchmark, SupplierBenchmarkListOut
from app.schemas.cash_flow import CashFlowPointOut
from app.schemas.cash_position import CashPositionOut
from app.services import (
    analytics,
    ap_aging,
    benchmark,
    cash_flow,
    cash_position,
    explore,
    report_writers,
    supplier_costs,
)

# Structural authorization (ADR-0024): the whole analytics surface reads the
# org's aggregated money data — router-level REPORT_READ. Previously only three
# endpoints checked it in-handler; the rest were open to any member (the
# security gap this order closes).
router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(require_perm(authz.Permission.REPORT_READ))],
)


@router.get("/cash-position", response_model=CashPositionOut)
async def get_cash_position(current: CurrentUser, db: DbSession):
    """AR + AP + reconciliation roll-up for the cash-position dashboard (Phase 15)."""
    return await cash_position.summary(db, current.org_id)


@router.get("/cash-flow", response_model=list[CashFlowPointOut])
async def get_cash_flow(
    current: CurrentUser,
    db: DbSession,
    months: int = Query(default=12, ge=1, le=36),
):
    """Monthly inflow / outflow / net cash-flow trend (Phase 19)."""
    return await cash_flow.monthly(db, current.org_id, months=months)


@router.get("/ap-aging", response_model=ApAgingOut)
async def get_ap_aging(current: CurrentUser, db: DbSession):
    """Payables worklist: open supplier invoices due soon / overdue (Phase 16b)."""
    items = await ap_aging.worklist(db, current.org_id)
    s = ap_aging.summarize(items)
    return ApAgingOut(
        currency=s.currency,
        due_soon_count=s.due_soon_count,
        due_soon_amount=s.due_soon_amount,
        overdue_count=s.overdue_count,
        overdue_amount=s.overdue_amount,
        other_currencies=list(s.other_currencies),
        items=[WorklistItemOut(**vars(it)) for it in items],
    )


# C1.7/WO-24: every report below sums money, so every one takes an optional
# `currency` filter — an explicit request wins, else the AR-reports pattern
# (`_pick_currency`) resolves the tenant's most-used currency. Never omit it
# silently: the response always names which currency was used and which
# others exist (`available_currencies`), never a blended cross-currency total.
_CurrencyQ = Query(default=None, min_length=3, max_length=3)


@router.get("/summary", response_model=SummaryOut)
async def get_summary(
    current: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
    currency: str | None = _CurrencyQ,
):
    return await analytics.summary(db, current.org_id, start, end, currency)


@router.get("/spend-over-time", response_model=SpendOverTimeOut)
async def get_spend_over_time(
    current: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
    currency: str | None = _CurrencyQ,
):
    return await analytics.spend_over_time(db, current.org_id, start, end, currency)


@router.get("/top-vendors", response_model=TopVendorsOut)
async def get_top_vendors(
    current: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    currency: str | None = _CurrencyQ,
):
    return await analytics.top_vendors(db, current.org_id, start, end, limit, currency)


@router.get("/by-category", response_model=ByCategoryOut)
async def get_by_category(
    current: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
    currency: str | None = _CurrencyQ,
):
    return await analytics.by_category(db, current.org_id, start, end, currency)


@router.get("/dimensions")
async def get_dimensions(current: CurrentUser):
    """The cost-allocation dimensions available to slice spend by."""
    return [{"key": k, "label": v} for k, v in DIMENSIONS.items()]


@router.get("/by-dimension", response_model=DimensionBreakdown)
async def get_by_dimension(
    current: CurrentUser,
    db: DbSession,
    dimension: str = Query(..., description=" | ".join(DIMENSION_KEYS)),
    start: date | None = None,
    end: date | None = None,
    currency: str | None = _CurrencyQ,
):
    if not is_dimension(dimension):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown dimension '{dimension}'")
    return await analytics.by_dimension(db, current.org_id, dimension, start, end, currency)


@router.get("/by-status", response_model=ByStatusOut)
async def get_by_status(
    current: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
    currency: str | None = _CurrencyQ,
):
    return await analytics.by_status(db, current.org_id, start, end, currency)


# --- Supplier cost analytics (WO-G phase 1) --------------------------------
# Read models over the tenant's own invoice lines; dict payloads (the service
# documents the shape) on the same REPORT_READ router — no new surface class.


@router.get("/supplier-costs/kpis")
async def get_supplier_cost_kpis(
    current: CurrentUser, db: DbSession, currency: str | None = _CurrencyQ
):
    return await supplier_costs.cost_kpis(db, current.org_id, currency=currency)


@router.get("/supplier-costs/changes")
async def get_supplier_cost_changes(
    current: CurrentUser,
    db: DbSession,
    currency: str | None = _CurrencyQ,
    window_days: int = Query(default=365, ge=30, le=1830),
    limit: int = Query(default=50, ge=1, le=500),
):
    return await supplier_costs.cost_changes(
        db, current.org_id, currency=currency, window_days=window_days, limit=limit
    )


@router.get("/supplier-costs/history")
async def get_supplier_cost_history(
    current: CurrentUser,
    db: DbSession,
    vendor_id: str,
    item: str,
    currency: str | None = _CurrencyQ,
    months: int = Query(default=12, ge=1, le=60),
):
    return await supplier_costs.price_history(
        db, current.org_id, vendor_id=vendor_id, item=item, currency=currency, months=months
    )


@router.get("/supplier-benchmark", response_model=SupplierBenchmarkListOut)
async def get_supplier_benchmark(
    current: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
    currency: str | None = _CurrencyQ,
):
    """Independent per-supplier scorecards."""
    return await benchmark.supplier_benchmarks(db, current.org_id, start, end, currency)


@router.get("/combined-benchmark", response_model=CombinedBenchmark)
async def get_combined_benchmark(
    current: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
    currency: str | None = _CurrencyQ,
):
    """Combined cross-supplier price benchmark per category + savings opportunity."""
    return await benchmark.combined_benchmark(db, current.org_id, start, end, currency)


@router.get("/fields")
async def get_explore_fields(current: CurrentUser):
    """The dimension + measure catalog for the self-service explore builder."""
    return explore.catalog()


@router.get("/explore")
async def get_explore(
    current: CurrentUser,
    db: DbSession,
    measure: str = Query(default="net"),
    dim: list[str] = Query(default_factory=list, description="up to 2 dimensions"),
    start: date | None = None,
    end: date | None = None,
    status_: str | None = Query(default=None, alias="status"),
    category: str | None = None,
    currency: str | None = None,
    country: str | None = None,
    vendor_id: str | None = None,
    sort: str = "auto",
    limit: int = Query(default=100, ge=1, le=1000),
    format: str = Query(default="json", pattern="^(json|csv|xlsx|pdf)$"),
):
    """Self-service pivot: pick a measure + up to two dimensions + filters."""
    q = explore.ExploreQuery(
        measure=measure,
        dimensions=dim,
        start=start,
        end=end,
        status=status_,
        category=category,
        currency=currency,
        country=country,
        vendor_id=vendor_id,
        sort=sort,
        limit=limit,
    )
    try:
        result = await explore.run(db, current.org_id, q)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    if format == "csv":
        return Response(
            content=explore.to_csv(result),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="explore.csv"'},
        )
    if format == "xlsx":
        return Response(
            content=report_writers.to_xlsx(result),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": content_disposition("explore.xlsx")},
        )
    if format == "pdf":
        return Response(
            content=report_writers.to_pdf(result),
            media_type="application/pdf",
            headers={"Content-Disposition": content_disposition("explore.pdf")},
        )
    return result
