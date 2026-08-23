"""Next-actions routes (WO-C). The whole surface is operator work — offers
to chase, invoices to collect, uploads to confirm, filings to prepare — so
the router is INVOICE_WRITE structurally: the people who can act on the
items are the people who see them (an employee's surface is the Schedule).
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.services import audit, next_actions

router = APIRouter(
    prefix="/next-actions",
    tags=["next-actions"],
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_WRITE))],
)


class ActionOut(BaseModel):
    kind: str
    ref_id: str
    title: str
    detail: str
    link: str
    age_days: int | None
    due_date: str | None
    dismissible: bool


class DismissIn(BaseModel):
    kind: str
    ref_id: str


class DeadlineIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cadence: str = "monthly"
    due_day: int = Field(default=15, ge=1, le=28)
    lead_days: int = Field(default=7, ge=0, le=90)


class DeadlineOut(BaseModel):
    id: str
    name: str
    cadence: str
    due_day: int
    lead_days: int
    last_done_period: str | None


def _raise(exc: next_actions.NextActionsError) -> NoReturn:
    if isinstance(exc, next_actions.NotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


def _deadline_out(d) -> DeadlineOut:
    return DeadlineOut(
        id=d.id,
        name=d.name,
        cadence=d.cadence,
        due_day=d.due_day,
        lead_days=d.lead_days,
        last_done_period=d.last_done_period,
    )


@router.get("", response_model=list[ActionOut])
async def list_actions(current: CurrentUser, db: DbSession):
    return [ActionOut(**vars(a)) for a in await next_actions.list_actions(db, current.org_id)]


@router.post("/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_action(body: DismissIn, current: CurrentUser, db: DbSession):
    try:
        await next_actions.dismiss(
            db, current.org_id, kind=body.kind, ref_id=body.ref_id, dismissed_by=current.email
        )
    except next_actions.NextActionsError as exc:
        _raise(exc)
    await audit.record(
        db,
        "next_action.dismiss",
        target_type=body.kind,
        target_id=body.ref_id,
    )
    await db.commit()


@router.get("/deadlines", response_model=list[DeadlineOut])
async def list_deadlines(current: CurrentUser, db: DbSession):
    return [_deadline_out(d) for d in await next_actions.list_deadlines(db, current.org_id)]


@router.post("/deadlines", response_model=DeadlineOut, status_code=status.HTTP_201_CREATED)
async def create_deadline(body: DeadlineIn, current: CurrentUser, db: DbSession):
    try:
        row = await next_actions.create_deadline(
            db,
            current.org_id,
            name=body.name,
            cadence=body.cadence,
            due_day=body.due_day,
            lead_days=body.lead_days,
            created_by=current.email,
        )
    except next_actions.NextActionsError as exc:
        _raise(exc)
    await audit.record(
        db, "next_action.deadline_create", target_type="deadline", target_id=row.id,
        meta={"name": row.name, "cadence": row.cadence},
    )
    await db.commit()
    await db.refresh(row)
    return _deadline_out(row)


@router.post("/deadlines/{deadline_id}/complete", response_model=DeadlineOut)
async def complete_deadline(deadline_id: str, current: CurrentUser, db: DbSession):
    try:
        row = await next_actions.complete_deadline(db, current.org_id, deadline_id)
    except next_actions.NextActionsError as exc:
        _raise(exc)
    await audit.record(
        db, "next_action.deadline_complete", target_type="deadline", target_id=row.id,
        meta={"period": row.last_done_period},
    )
    await db.commit()
    await db.refresh(row)
    return _deadline_out(row)


@router.delete("/deadlines/{deadline_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deadline(deadline_id: str, current: CurrentUser, db: DbSession):
    try:
        row = await next_actions.delete_deadline(db, current.org_id, deadline_id)
    except next_actions.NextActionsError as exc:
        _raise(exc)
    # The audit meta carries WHAT was removed — after commit it is the only trace.
    await audit.record(
        db, "next_action.deadline_delete", target_type="deadline", target_id=deadline_id,
        meta={"name": row.name, "cadence": row.cadence},
    )
    await db.commit()
