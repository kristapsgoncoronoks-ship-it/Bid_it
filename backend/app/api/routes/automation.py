"""Admin automation rules API (WO-J) — org configuration, SETTINGS_MANAGE.

Thin controllers over `services/automation.py`. Publishing is the moment a
rule becomes real: it snapshots an immutable version, and every run row
names the version that acted. Dry-run answers "what WOULD this do right
now" with zero side effects.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.automation import ACTIONS, TRIGGERS
from app.services import audit, automation

router = APIRouter(
    prefix="/automation",
    tags=["automation"],
    dependencies=[Depends(require_perm(authz.Permission.SETTINGS_MANAGE))],
)


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    trigger: str
    condition: Any = None
    actions: list[dict] = Field(default_factory=list)
    fire_policy: str = "once_per_record"
    cooldown_hours: int | None = Field(default=None, ge=1, le=8760)


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    trigger: str | None = None
    condition: Any = None
    set_condition: bool = False
    actions: list[dict] | None = None
    fire_policy: str | None = None
    cooldown_hours: int | None = Field(default=None, ge=1, le=8760)


class StatusIn(BaseModel):
    status: str


class RuleOut(BaseModel):
    id: str
    name: str
    trigger: str
    condition: Any
    actions: list[dict]
    status: str
    fire_policy: str
    cooldown_hours: int | None
    published_version: int | None
    created_at: str


def _out(r) -> RuleOut:
    return RuleOut(
        id=r.id,
        name=r.name,
        trigger=r.trigger,
        condition=json.loads(r.condition_json) if r.condition_json else None,
        actions=json.loads(r.actions_json),
        status=r.status,
        fire_policy=r.fire_policy,
        cooldown_hours=r.cooldown_hours,
        published_version=r.published_version,
        created_at=r.created_at.isoformat(),
    )


def _raise(exc: automation.AutomationError) -> NoReturn:
    if isinstance(exc, automation.NotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/meta")
async def automation_meta(current: CurrentUser):
    """The builder UI's vocabulary: the closed trigger and action sets."""
    return {"triggers": TRIGGERS, "actions": list(ACTIONS)}


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(current: CurrentUser, db: DbSession):
    from sqlalchemy import select

    from app.models.automation import AutomationRule

    rows = await db.scalars(
        select(AutomationRule)
        .where(AutomationRule.org_id == current.org_id)
        .order_by(AutomationRule.created_at)
    )
    return [_out(r) for r in rows]


@router.post("/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(body: RuleIn, current: CurrentUser, db: DbSession):
    try:
        row = await automation.create_rule(
            db,
            current.org_id,
            name=body.name,
            trigger=body.trigger,
            condition=body.condition,
            actions=body.actions,
            fire_policy=body.fire_policy,
            cooldown_hours=body.cooldown_hours,
            created_by=current.email,
        )
    except automation.AutomationError as exc:
        _raise(exc)
    await audit.record(
        db,
        "automation.rule_create",
        target_type="automation_rule",
        target_id=row.id,
        meta={"name": row.name, "trigger": row.trigger},
    )
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: str, body: RulePatch, current: CurrentUser, db: DbSession):
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.trigger is not None:
        fields["trigger"] = body.trigger
    if body.set_condition:
        fields["condition"] = body.condition
    if body.actions is not None:
        fields["actions"] = body.actions
    if body.fire_policy is not None:
        fields["fire_policy"] = body.fire_policy
    if body.cooldown_hours is not None:
        fields["cooldown_hours"] = body.cooldown_hours
    try:
        row = await automation.update_rule(db, current.org_id, rule_id, **fields)
    except automation.AutomationError as exc:
        _raise(exc)
    await audit.record(
        db,
        "automation.rule_update",
        target_type="automation_rule",
        target_id=rule_id,
        meta={"fields": sorted(fields)},
    )
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.post("/rules/{rule_id}/publish", response_model=RuleOut)
async def publish_rule(rule_id: str, current: CurrentUser, db: DbSession):
    try:
        row = await automation.publish_rule(db, current.org_id, rule_id, published_by=current.email)
    except automation.AutomationError as exc:
        _raise(exc)
    await audit.record(
        db,
        "automation.rule_publish",
        target_type="automation_rule",
        target_id=rule_id,
        meta={"version": row.published_version},
    )
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.post("/rules/{rule_id}/revert/{version}", response_model=RuleOut)
async def revert_rule(rule_id: str, version: int, current: CurrentUser, db: DbSession):
    try:
        row = await automation.revert_rule(
            db, current.org_id, rule_id, version, published_by=current.email
        )
    except automation.AutomationError as exc:
        _raise(exc)
    await audit.record(
        db,
        "automation.rule_revert",
        target_type="automation_rule",
        target_id=rule_id,
        meta={"from_version": version, "new_version": row.published_version},
    )
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.put("/rules/{rule_id}/status", response_model=RuleOut)
async def set_rule_status(rule_id: str, body: StatusIn, current: CurrentUser, db: DbSession):
    try:
        row = await automation.set_status(db, current.org_id, rule_id, body.status)
    except automation.AutomationError as exc:
        _raise(exc)
    await audit.record(
        db,
        "automation.rule_status",
        target_type="automation_rule",
        target_id=rule_id,
        meta={"status": row.status},
    )
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.post("/rules/{rule_id}/dry-run")
async def dry_run_rule(rule_id: str, current: CurrentUser, db: DbSession):
    """What WOULD fire right now — evaluated for real, sent nowhere."""
    try:
        rule = await automation._rule(db, current.org_id, rule_id)
        outcomes = await automation.evaluate_rule(db, current.org_id, rule, dry_run=True)
    except automation.AutomationError as exc:
        _raise(exc)
    return {"outcomes": outcomes}


@router.get("/runs")
async def list_runs(
    current: CurrentUser, db: DbSession, limit: int = Query(default=100, ge=1, le=500)
):
    rows = await automation.list_runs(db, current.org_id, limit=limit)
    return [
        {
            "id": r.id,
            "rule_id": r.rule_id,
            "version": r.version,
            "ref_id": r.ref_id,
            "status": r.status,
            "detail": json.loads(r.detail_json) if r.detail_json else None,
            "at": r.created_at.isoformat(),
        }
        for r in rows
    ]
