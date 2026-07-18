from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.analytics import (
    CategorySpend,
    StatusBucket,
    SummaryOut,
    TimeBucket,
    VendorSpend,
)
from app.schemas.benchmark import CombinedBenchmark, SupplierBenchmark
from app.services import analytics, benchmark

router = APIRouter(prefix="/analytics", tags=["analytics"])


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
