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


# --- Slice 2: backfill free-text dimensions → master-table links ------------
#
# Invoices carry both the legacy free-text tag (`cost_center`) and the new FK
# (`cost_center_id`). This resolves the tag to a master row by CODE (then NAME),
# case-insensitively, for rows not yet linked — the dual-read transition step.
# It matches against masters of ANY status (a historical link to an archived
# master is valid), invents nothing (an unmatched tag stays NULL), and is
# idempotent (re-running only touches still-unlinked rows).

# (free-text column, FK column, master model) for the three linkable dimensions.
_LINKABLE = (
    ("cost_center", "cost_center_id", CostCenter),
    ("department", "department_id", Department),
    ("project", "project_id", Project),
)
_MASTER_BY_DIM = {dim: master for dim, _, master in _LINKABLE}
_FK_BY_DIM = {dim: fk for dim, fk, _ in _LINKABLE}


async def resolve_link_id(
    db: AsyncSession, org_id: str, dimension: str, value: str | None
) -> str | None:
    """Resolve ONE free-text dimension value to its master-row id — by code then
    name, case-insensitively. Returns None when the value is empty, the dimension
    has no master table (vehicle/property_ref), or nothing matches (invents
    nothing). This is the write-path counterpart of the backfill: same matching."""
    master = _MASTER_BY_DIM.get(dimension)
    if master is None or not value or not value.strip():
        return None
    v = value.strip().lower()
    hit = await db.scalar(
        select(master.id).where(master.org_id == org_id, func.lower(master.code) == v)
    )
    if hit is not None:
        return hit
    return await db.scalar(
        select(master.id).where(master.org_id == org_id, func.lower(master.name) == v)
    )


async def apply_links(db: AsyncSession, org_id: str, obj, dimensions) -> None:
    """Set `obj.<dim>_id` from the object's current free-text `obj.<dim>` for each
    given dimension (non-linkable dimensions are ignored). Use at invoice/expense
    create+update so a new/edited tag is linked immediately, not only on the next
    backfill sweep. A cleared or unmatched tag sets the link back to None."""
    for dim in dimensions:
        fk = _FK_BY_DIM.get(dim)
        if fk is None:
            continue
        setattr(obj, fk, await resolve_link_id(db, org_id, dim, getattr(obj, dim)))


async def _code_name_index(db: AsyncSession, model, org_id: str) -> dict[str, str]:
    """{lower(code|name): id} for one org's master rows — code wins on collision."""
    rows = (
        await db.execute(select(model.code, model.name, model.id).where(model.org_id == org_id))
    ).all()
    by_name: dict[str, str] = {}
    by_code: dict[str, str] = {}
    for code, name, mid in rows:
        by_code.setdefault(code.lower(), mid)
        by_name.setdefault(name.lower(), mid)
    return {**by_name, **by_code}  # code entries override name entries


async def _backfill_model_links(db: AsyncSession, org_id: str, model) -> dict[str, int]:
    """Resolve unlinked free-text dimension tags to master rows for one org on one
    model (Invoice or ExpenseItem — both carry org_id + the same dim columns).
    Returns the count newly linked per dimension. Commits once if anything linked."""
    from sqlalchemy import and_, or_

    counts = {dim: 0 for dim, _, _ in _LINKABLE}
    indexes = {dim: await _code_name_index(db, master, org_id) for dim, _, master in _LINKABLE}

    unlinked = or_(
        *[
            and_(getattr(model, dim).is_not(None), getattr(model, fk).is_(None))
            for dim, fk, _ in _LINKABLE
        ]
    )
    rows = list(await db.scalars(select(model).where(model.org_id == org_id, unlinked)))

    for row in rows:
        for dim, fk, _ in _LINKABLE:
            if getattr(row, fk) is not None:
                continue
            tag = getattr(row, dim)
            if not tag:
                continue
            mid = indexes[dim].get(tag.strip().lower())
            if mid is not None:
                setattr(row, fk, mid)
                counts[dim] += 1

    if any(counts.values()):
        await db.commit()
    return counts


async def backfill_invoice_links(db: AsyncSession, org_id: str) -> dict[str, int]:
    """Link each unlinked invoice dimension tag to its master row (Slice 2)."""
    from app.models.invoice import Invoice

    return await _backfill_model_links(db, org_id, Invoice)


async def backfill_expense_item_links(db: AsyncSession, org_id: str) -> dict[str, int]:
    """Link each unlinked expense-item dimension tag to its master row (Slice 2b)."""
    from app.models.expense import ExpenseItem

    return await _backfill_model_links(db, org_id, ExpenseItem)
