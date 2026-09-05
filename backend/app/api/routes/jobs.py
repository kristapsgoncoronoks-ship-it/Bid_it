from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.job import Job
from app.schemas.jobs import JobEnqueue, JobOut
from app.services import job_handlers, jobs

# Structural authorization (ADR-0024): the background-job queue is operational
# tooling — router-level SETTINGS_MANAGE (previously only enqueue/retry checked
# it; list/get were open to any member).
router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_perm(authz.Permission.SETTINGS_MANAGE))],
)


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
    """Requeue a dead/failed job (resets its attempt counter). A running or
    finished job answers 409 — requeuing a RUNNING job would make it claimable
    by a second worker and run it twice in parallel (BE-001)."""
    job = await _load(db, current.org_id, job_id)
    try:
        return JobOut.model_validate(await jobs.retry(db, job))
    except jobs.JobNotRetryable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
