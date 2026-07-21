"""Cost-allocation master data service (Slice 1).

The working use case behind the master-data schema: create / list / update /
archive Departments, Cost centers, Projects — with the status-transition rules,
**optimistic concurrency**, and same-org validation the data model requires.

All queries are tenant-scoped by the caller's `org_id`; the ORM guard + RLS are
the belt-and-braces. Master data is **archived, never hard-deleted** (historical
cost allocations must still resolve their code/name).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.costing import CostCenter, Department, Project

# Allowed status transitions per entity (enforced here, not just in the DB).
_TRANSITIONS = {
    "department": {"active": {"archived"}, "archived": {"active"}},
    "cost_center": {"active": {"archived"}, "archived": {"active"}},
    "project": {
        "active": {"closed", "archived"},
        "closed": {"active", "archived"},
        "archived": {"active"},
    },
}


class CostingError(Exception):
    """Business-rule violation (bad transition, stale write, cross-org ref)."""


class ConcurrencyError(CostingError):
    """Optimistic-concurrency conflict: the row changed since it was read."""


def _kind(model) -> str:
    return {"departments": "department", "cost_centers": "cost_center", "projects": "project"}[
        model.__tablename__
    ]


async def _code_taken(db: AsyncSession, model, org_id: str, code: str) -> bool:
    return bool(
        await db.scalar(
            select(model.id).where(model.org_id == org_id, func.lower(model.code) == code.lower())
        )
    )


# --- Departments -----------------------------------------------------------


async def create_department(db: AsyncSession, org_id: str, *, code: str, name: str) -> Department:
    if await _code_taken(db, Department, org_id, code):
        raise CostingError(f"department code '{code}' already exists")
    dep = Department(org_id=org_id, code=code, name=name)
    db.add(dep)
    await db.commit()
    await db.refresh(dep)
    return dep


async def create_cost_center(
    db: AsyncSession, org_id: str, *, code: str, name: str, department_id: str | None = None
) -> CostCenter:
    if await _code_taken(db, CostCenter, org_id, code):
        raise CostingError(f"cost center code '{code}' already exists")
    if department_id is not None:
        # Same-org validation (app layer); the composite FK is the DB backstop.
        dep = await db.scalar(
            select(Department).where(Department.org_id == org_id, Department.id == department_id)
        )
        if dep is None:
            raise CostingError("department not found in this workspace")
    cc = CostCenter(org_id=org_id, code=code, name=name, department_id=department_id)
    db.add(cc)
    await db.commit()
    await db.refresh(cc)
    return cc


async def create_project(
    db: AsyncSession, org_id: str, *, code: str, name: str, start_date=None, end_date=None
) -> Project:
    if await _code_taken(db, Project, org_id, code):
        raise CostingError(f"project code '{code}' already exists")
    pr = Project(org_id=org_id, code=code, name=name, start_date=start_date, end_date=end_date)
    db.add(pr)
    await db.commit()
    await db.refresh(pr)
    return pr


# --- listing (active-only by default) --------------------------------------


async def list_entities(
    db: AsyncSession, model, org_id: str, *, include_archived: bool = False
) -> list:
    stmt = select(model).where(model.org_id == org_id)
    if not include_archived:
        stmt = stmt.where(model.status != "archived")
    return list(await db.scalars(stmt.order_by(model.code.asc())))


# --- update with optimistic concurrency ------------------------------------


async def rename(
    db: AsyncSession, model, org_id: str, entity_id: str, *, name: str, expected_version: int
) -> object:
    row = await _load(db, model, org_id, entity_id)
    _check_version(row, expected_version)
    row.name = name
    row.version += 1
    await db.commit()
    await db.refresh(row)
    return row


async def set_status(
    db: AsyncSession, model, org_id: str, entity_id: str, *, status: str, expected_version: int
) -> object:
    row = await _load(db, model, org_id, entity_id)
    _check_version(row, expected_version)
    allowed = _TRANSITIONS[_kind(model)].get(row.status, set())
    if status != row.status and status not in allowed:
        raise CostingError(f"cannot move {_kind(model)} from '{row.status}' to '{status}'")
    row.status = status
    row.archived_at = datetime.now(UTC) if status == "archived" else None
    row.version += 1
    await db.commit()
    await db.refresh(row)
    return row


async def _load(db: AsyncSession, model, org_id: str, entity_id: str):
    row = await db.scalar(select(model).where(model.org_id == org_id, model.id == entity_id))
    if row is None:
        raise CostingError(f"{_kind(model)} not found")
    return row


def _check_version(row, expected: int) -> None:
    if row.version != expected:
        raise ConcurrencyError(
            f"stale write: expected version {expected}, current is {row.version}"
        )
