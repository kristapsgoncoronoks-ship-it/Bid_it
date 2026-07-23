from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.core.dimensions import DIMENSIONS, is_dimension
from app.schemas.analytics import (
    CategorySpend,
    DimensionBreakdown,
    StatusBucket,
    SummaryOut,
    TimeBucket,
    VendorSpend,
)
from app.schemas.ap_aging import ApAgingOut, WorklistItemOut
from app.schemas.benchmark import CombinedBenchmark, SupplierBenchmark
from app.schemas.cash_position import CashPositionOut
from app.services import analytics, ap_aging, benchmark, cash_position, explore

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/cash-position", response_model=CashPositionOut)
async def get_cash_position(current: CurrentUser, db: DbSession):
    """AR + AP + reconciliation roll-up for the cash-position dashboard (Phase 15)."""
    authz.require(current, authz.Permission.REPORT_READ)
    return await cash_position.summary(db, current.org_id)


@router.get("/ap-aging", response_model=ApAgingOut)
async def get_ap_aging(current: CurrentUser, db: DbSession):
    """Payables worklist: open supplier invoices due soon / overdue (Phase 16b)."""
    authz.require(current, authz.Permission.REPORT_READ)
    items = await ap_aging.worklist(db, current.org_id)
    s = ap_aging.summarize(items)
    return ApAgingOut(
        due_soon_count=s.due_soon_count,
        due_soon_amount=s.due_soon_amount,
        overdue_count=s.overdue_count,
        overdue_amount=s.overdue_amount,
        items=[WorklistItemOut(**vars(it)) for it in items],
    )


@router.get("/summary", response_model=SummaryOut)
async def get_summary(
    current: CurrentUser, db: DbSession, start: date | None = None, end: date | None = None
):
    return await analytics.summary(db, current.org_id, start, end)


@router.get("/spend-over-time", response_model=list[TimeBucket])
async def get_spend_over_time(
    current: CurrentUser, db: DbSession, start: date | None = None, end: date | None = None
):
    return await analytics.spend_over_time(db, current.org_id, start, end)


@router.get("/top-vendors", response_model=list[VendorSpend])
async def get_top_vendors(
    current: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(default=10, ge=1, le=50),
):
    return await analytics.top_vendors(db, current.org_id, start, end, limit)


@router.get("/by-category", response_model=list[CategorySpend])
async def get_by_category(
    current: CurrentUser, db: DbSession, start: date | None = None, end: date | None = None
):
    return await analytics.by_category(db, current.org_id, start, end)


@router.get("/dimensions")
async def get_dimensions(current: CurrentUser):
    """The cost-allocation dimensions available to slice spend by."""
    return [{"key": k, "label": v} for k, v in DIMENSIONS.items()]


@router.get("/by-dimension", response_model=DimensionBreakdown)
async def get_by_dimension(
    current: CurrentUser,
    db: DbSession,
    dimension: str = Query(
        ..., description="cost_center | department | project | vehicle | property_ref"
    ),
    start: date | None = None,
    end: date | None = None,
):
    if not is_dimension(dimension):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown dimension '{dimension}'")
    return await analytics.by_dimension(db, current.org_id, dimension, start, end)


@router.get("/by-status", response_model=list[StatusBucket])
async def get_by_status(
    current: CurrentUser, db: DbSession, start: date | None = None, end: date | None = None
):
    return await analytics.by_status(db, current.org_id, start, end)


@router.get("/supplier-benchmark", response_model=list[SupplierBenchmark])
async def get_supplier_benchmark(
    current: CurrentUser, db: DbSession, start: date | None = None, end: date | None = None
):
    """Independent per-supplier scorecards."""
    return await benchmark.supplier_benchmarks(db, current.org_id, start, end)


@router.get("/combined-benchmark", response_model=CombinedBenchmark)
async def get_combined_benchmark(
    current: CurrentUser, db: DbSession, start: date | None = None, end: date | None = None
):
    """Combined cross-supplier price benchmark per category + savings opportunity."""
    return await benchmark.combined_benchmark(db, current.org_id, start, end)


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
    format: str = Query(default="json", pattern="^(json|csv)$"),
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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    if format == "csv":
        return Response(
            content=explore.to_csv(result),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="explore.csv"'},
        )
    return result
