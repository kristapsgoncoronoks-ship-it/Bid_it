"""Cost-allocation master data (Slice 1 of the data model). Exercises every
required design principle: tenant boundary, unique/duplicate rules, DB-level
cross-tenant FK protection, status lifecycle + soft delete, and optimistic
concurrency."""

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.costing import CostCenter, Department, Project
from app.models.organization import Organization
from app.services import costing


async def _org(db):
    return await db.scalar(select(Organization.id))


# --- uniqueness / duplicate rules ------------------------------------------


@pytest.mark.asyncio
async def test_unique_code_per_org_db_constraint(auth_client, db_session):
    org_id = await _org(db_session)
    db_session.add(Department(org_id=org_id, code="OPS", name="Operations"))
    db_session.add(Department(org_id=org_id, code="OPS", name="Ops dup"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_service_rejects_duplicate_code(auth_client, db_session):
    org_id = await _org(db_session)
    await costing.create_department(db_session, org_id, code="FIN", name="Finance")
    with pytest.raises(costing.CostingError):
        await costing.create_department(
            db_session, org_id, code="fin", name="dup (case-insensitive)"
        )


# --- tenant boundary -------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_scoped_listing(auth_client, db_session):
    org_a = await _org(db_session)
    org_b = Organization(name="Other Co")
    db_session.add(org_b)
    await db_session.flush()
    await costing.create_department(db_session, org_a, code="A1", name="A dept")
    await costing.create_department(db_session, org_b.id, code="B1", name="B dept")

    a_codes = [d.code for d in await costing.list_entities(db_session, Department, org_a)]
    b_codes = [d.code for d in await costing.list_entities(db_session, Department, org_b.id)]
    assert a_codes == ["A1"] and b_codes == ["B1"]  # neither sees the other's


# --- cross-tenant FK protection (structural, at the DB) --------------------


@pytest.mark.asyncio
async def test_cross_tenant_fk_blocked_at_db():
    """A cost center in org B cannot reference a department in org A — the
    composite FK (org_id, department_id) -> departments(org_id, id) makes it
    structurally impossible. Proven on a FK-enforcing SQLite engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as db:
            a, b = Organization(name="A"), Organization(name="B")
            db.add_all([a, b])
            await db.flush()
            dep_a = Department(org_id=a.id, code="OPS", name="Ops")
            db.add(dep_a)
            await db.flush()
            # org B cost center pointing at org A's department → composite FK violation
            db.add(CostCenter(org_id=b.id, code="CC", name="x", department_id=dep_a.id))
            with pytest.raises(IntegrityError):
                await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_service_rejects_cross_org_department(auth_client, db_session):
    org_a = await _org(db_session)
    org_b = Organization(name="B Co")
    db_session.add(org_b)
    await db_session.flush()
    dep_b = await costing.create_department(db_session, org_b.id, code="OPS", name="Ops")
    with pytest.raises(costing.CostingError):
        await costing.create_cost_center(
            db_session, org_a, code="CC-1", name="x", department_id=dep_b.id
        )


@pytest.mark.asyncio
async def test_same_org_department_ref_ok(auth_client, db_session):
    org_id = await _org(db_session)
    dep = await costing.create_department(db_session, org_id, code="OPS", name="Ops")
    cc = await costing.create_cost_center(
        db_session, org_id, code="CC-1", name="Fleet", department_id=dep.id
    )
    assert cc.department_id == dep.id


# --- status lifecycle + soft delete ----------------------------------------


@pytest.mark.asyncio
async def test_archive_is_soft_delete(auth_client, db_session):
    org_id = await _org(db_session)
    dep = await costing.create_department(db_session, org_id, code="OPS", name="Ops")
    await costing.set_status(
        db_session, Department, org_id, dep.id, status="archived", expected_version=dep.version
    )

    await db_session.refresh(dep)
    assert dep.status == "archived" and dep.archived_at is not None
    # Row still exists; excluded from the default listing, included on demand.
    assert dep.id not in [d.id for d in await costing.list_entities(db_session, Department, org_id)]
    assert dep.id in [
        d.id
        for d in await costing.list_entities(db_session, Department, org_id, include_archived=True)
    ]


@pytest.mark.asyncio
async def test_invalid_transition_rejected(auth_client, db_session):
    org_id = await _org(db_session)
    dep = await costing.create_department(db_session, org_id, code="OPS", name="Ops")
    with pytest.raises(costing.CostingError):
        await costing.set_status(
            db_session, Department, org_id, dep.id, status="closed", expected_version=dep.version
        )  # 'closed' invalid for department


@pytest.mark.asyncio
async def test_project_lifecycle(auth_client, db_session):
    org_id = await _org(db_session)
    pr = await costing.create_project(db_session, org_id, code="PRJ-1", name="Baltic")
    pr = await costing.set_status(
        db_session, Project, org_id, pr.id, status="closed", expected_version=pr.version
    )
    assert pr.status == "closed"
    pr = await costing.set_status(
        db_session, Project, org_id, pr.id, status="archived", expected_version=pr.version
    )
    assert pr.status == "archived"


# --- optimistic concurrency ------------------------------------------------


@pytest.mark.asyncio
async def test_optimistic_concurrency(auth_client, db_session):
    org_id = await _org(db_session)
    dep = await costing.create_department(db_session, org_id, code="OPS", name="Ops")
    assert dep.version == 1
    dep = await costing.rename(
        db_session, Department, org_id, dep.id, name="Operations", expected_version=1
    )
    assert dep.version == 2 and dep.name == "Operations"
    # A second writer holding the stale version=1 is rejected.
    with pytest.raises(costing.ConcurrencyError):
        await costing.rename(
            db_session, Department, org_id, dep.id, name="Stale", expected_version=1
        )
