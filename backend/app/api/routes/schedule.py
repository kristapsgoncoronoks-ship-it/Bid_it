"""Work-planning schedule routes (WO-A, docs/design/work-calendar.md phase A).

Permission shape, decided in the design doc and mirrored here structurally:

- READ is INVOICE_READ — every business role has it, including employees.
  The list endpoint then narrows: without planning rights (INVOICE_WRITE)
  a caller receives only their OWN assignments, which IS the "My work" view.
- PLANNING (create/edit) is INVOICE_WRITE — scheduling people is bookkeeping-
  grade day-to-day work, not org configuration (the costing precedent).
- TRANSITION is INVOICE_READ structurally: the assignee may confirm/finish
  their own assignment; anything further requires planning rights — enforced
  in the service, failing OPAQUELY (§4.4) so probing ids learns nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.services import audit, scheduling, team

router = APIRouter(
    prefix="/schedule",
    tags=["schedule"],
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_READ))],
)

_PLANNING = [Depends(require_perm(authz.Permission.INVOICE_WRITE))]


class AssignmentIn(BaseModel):
    project_id: str
    assignee_user_id: str
    starts_at: datetime
    ends_at: datetime
    all_day: bool = False
    note: str | None = Field(default=None, max_length=2000)


class AssignmentPatch(BaseModel):
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    all_day: bool | None = None
    note: str | None = Field(default=None, max_length=2000)
    clear_note: bool = False
    assignee_user_id: str | None = None


class TransitionIn(BaseModel):
    status: str


class AssignmentOut(BaseModel):
    id: str
    project_id: str
    assignee_user_id: str
    assignee_email: str
    starts_at: str
    ends_at: str
    all_day: bool
    status: str
    note: str | None
    created_by: str


class AssignmentWriteOut(BaseModel):
    """A write's result plus the ADVISORY overlap warnings (never blocking)."""

    assignment: AssignmentOut
    overlaps: list[AssignmentOut]


class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str | None


def _out(a) -> AssignmentOut:
    return AssignmentOut(
        id=a.id,
        project_id=a.project_id,
        assignee_user_id=a.assignee_user_id,
        assignee_email=a.assignee_email,
        starts_at=a.starts_at.isoformat(),
        ends_at=a.ends_at.isoformat(),
        all_day=a.all_day,
        status=a.status,
        note=a.note,
        created_by=a.created_by,
    )


def _raise(exc: scheduling.SchedulingError) -> NoReturn:
    if isinstance(exc, scheduling.NotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/assignments", response_model=list[AssignmentOut])
async def list_assignments(
    start: datetime,
    end: datetime,
    current: CurrentUser,
    db: DbSession,
    assignee_user_id: str | None = None,
    project_id: str | None = None,
):
    """The calendar read. Planners see the workspace; everyone else sees
    exactly their own rows regardless of the filters they pass."""
    if not authz.has(current, authz.Permission.INVOICE_WRITE):
        assignee_user_id = current.id
    rows = await scheduling.list_window(
        db,
        current.org_id,
        start=start,
        end=end,
        assignee_user_id=assignee_user_id,
        project_id=project_id,
    )
    return [_out(a) for a in rows]


@router.get("/members", response_model=list[MemberOut], dependencies=_PLANNING)
async def plannable_members(current: CurrentUser, db: DbSession):
    """The assignee picker. Planning-rights-gated (a finance manager holds no
    MEMBER_READ; this exposes exactly what a planner needs: who can be
    scheduled, nothing about roles or invitations)."""
    members = await team.list_members(db, current.org_id)
    return [
        MemberOut(user_id=m.user_id, email=m.email or "", name=m.name)
        for m in members
        if m.status == "active"
    ]


@router.post(
    "/assignments",
    response_model=AssignmentWriteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_PLANNING,
)
async def create_assignment(body: AssignmentIn, current: CurrentUser, db: DbSession):
    try:
        row, overlaps = await scheduling.create(
            db,
            current.org_id,
            project_id=body.project_id,
            assignee_user_id=body.assignee_user_id,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            all_day=body.all_day,
            note=body.note,
            created_by=current.email,
        )
    except scheduling.SchedulingError as exc:
        _raise(exc)
    await audit.record(
        db,
        "assignment.create",
        target_type="project",
        target_id=row.project_id,
        meta={
            "assignment_id": row.id,
            "assignee": row.assignee_email,
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(),
        },
    )
    await db.commit()
    await db.refresh(row)
    return AssignmentWriteOut(assignment=_out(row), overlaps=[_out(o) for o in overlaps])


@router.patch("/assignments/{assignment_id}", response_model=AssignmentWriteOut, dependencies=_PLANNING)
async def update_assignment(
    assignment_id: str, body: AssignmentPatch, current: CurrentUser, db: DbSession
):
    try:
        row, overlaps = await scheduling.update(
            db,
            current.org_id,
            assignment_id,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            all_day=body.all_day,
            note=body.note,
            clear_note=body.clear_note,
            assignee_user_id=body.assignee_user_id,
        )
    except scheduling.SchedulingError as exc:
        _raise(exc)
    await audit.record(
        db,
        "assignment.update",
        target_type="project",
        target_id=row.project_id,
        meta={
            "assignment_id": row.id,
            "assignee": row.assignee_email,
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(),
        },
    )
    await db.commit()
    await db.refresh(row)
    return AssignmentWriteOut(assignment=_out(row), overlaps=[_out(o) for o in overlaps])


@router.post("/assignments/{assignment_id}/transition", response_model=AssignmentOut)
async def transition_assignment(
    assignment_id: str, body: TransitionIn, current: CurrentUser, db: DbSession
):
    """Lifecycle move. Structurally INVOICE_READ: an assignee confirms or
    finishes THEIR OWN assignment; every other move needs planning rights
    (enforced in the service, opaque on failure)."""
    try:
        row = await scheduling.transition(
            db,
            current.org_id,
            assignment_id,
            body.status,
            actor_user_id=current.id,
            actor_may_plan=authz.has(current, authz.Permission.INVOICE_WRITE),
        )
    except scheduling.SchedulingError as exc:
        _raise(exc)
    await audit.record(
        db,
        "assignment.transition",
        target_type="project",
        target_id=row.project_id,
        meta={"assignment_id": row.id, "status": row.status, "assignee": row.assignee_email},
    )
    await db.commit()
    await db.refresh(row)
    return _out(row)
