from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_admin_or_above
from app.models.job import Job
from app.schemas.jobs import JobEnqueue, JobOut
from app.services import job_handlers, jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _require_admin(current: CurrentUser) -> None:
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can manage background jobs")


@router.get("", response_model=list[JobOut])
async def list_jobs(
    current: CurrentUser,
    db: DbSession,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    """This tenant's background jobs, newest first (optionally by status)."""
    stmt = select(Job).where(Job.org_id == current.org_id)
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
    rows = await db.scalars(stmt.order_by(Job.created_at.desc()).limit(limit))
    return [JobOut.model_validate(r) for r in rows]


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def enqueue_job(body: JobEnqueue, current: CurrentUser, db: DbSession):
    """Enqueue a background job. Only a safe allowlist of kinds is user-facing."""
    _require_admin(current)
    if body.kind not in job_handlers.USER_ENQUEUEABLE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown or non-enqueueable job kind '{body.kind}'. "
            f"Allowed: {', '.join(job_handlers.USER_ENQUEUEABLE)}.",
        )
    job = await jobs.enqueue(
        db,
        body.kind,
        body.payload,
        org_id=current.org_id,
        idempotency_key=body.idempotency_key,
    )
    return JobOut.model_validate(job)


async def _load(db: DbSession, org_id: str, job_id: str) -> Job:
    job = await db.scalar(select(Job).where(Job.id == job_id, Job.org_id == org_id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, current: CurrentUser, db: DbSession):
    return JobOut.model_validate(await _load(db, current.org_id, job_id))


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: str, current: CurrentUser, db: DbSession):
    """Requeue a dead/failed job (resets its attempt counter)."""
    _require_admin(current)
    job = await _load(db, current.org_id, job_id)
    return JobOut.model_validate(await jobs.retry(db, job))
