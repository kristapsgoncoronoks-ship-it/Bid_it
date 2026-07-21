"""Slice 2 — normalise invoice cost-allocation dimensions to the master tables.

Covers the composite cross-tenant FK guard (at the DB), the dual-read backfill
(code/name resolution, unmatched-stays-null, idempotent), and running it through
the background job handler.
"""

from datetime import date

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.costing import CostCenter
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.vendor import Vendor
from app.services import costing


async def _org(db):
    return await db.scalar(select(Organization.id))


async def _vendor(db, org_id):
    v = Vendor(org_id=org_id, name="Acme Fuels")
    db.add(v)
    await db.flush()
    return v.id


def _inv(org_id, vendor_id, number, **dims):
    return Invoice(
        org_id=org_id,
        vendor_id=vendor_id,
        invoice_number=number,
        issue_date=date(2026, 1, 1),
        **dims,
    )


# --- cross-tenant FK protection (structural, at the DB) --------------------


@pytest.mark.asyncio
async def test_cross_tenant_link_blocked_at_db():
    """An invoice in org B cannot reference a cost center in org A — the composite
    FK (org_id, cost_center_id) → cost_centers(org_id, id) forbids it."""
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
            cc_a = CostCenter(org_id=a.id, code="FLEET", name="Fleet")
            db.add(cc_a)
            await db.flush()
            vb = await _vendor(db, b.id)
            # org B invoice pointing at org A's cost center → composite FK violation.
            db.add(_inv(b.id, vb, "B-1", cost_center_id=cc_a.id))
            with pytest.raises(IntegrityError):
                await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_org_link_ok(auth_client, db_session):
    org_id = await _org(db_session)
    vid = await _vendor(db_session, org_id)
    cc = await costing.create_cost_center(db_session, org_id, code="FLEET", name="Fleet")
    inv = _inv(org_id, vid, "OK-1", cost_center_id=cc.id)
    db_session.add(inv)
    await db_session.commit()
    await db_session.refresh(inv)
    assert inv.cost_center_id == cc.id


# --- dual-read backfill ----------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_resolves_by_code_and_leaves_unmatched_null(auth_client, db_session):
    org_id = await _org(db_session)
    vid = await _vendor(db_session, org_id)
    await costing.create_cost_center(db_session, org_id, code="FLEET", name="Vehicle fleet")
    dep = await costing.create_department(db_session, org_id, code="OPS", name="Operations")

    # Two invoices tagged by free text; one dim tag has no matching master.
    db_session.add_all(
        [
            _inv(org_id, vid, "I-1", cost_center="fleet", department="OPS"),  # both resolve
            _inv(org_id, vid, "I-2", cost_center="UNKNOWN"),  # no master → stays null
        ]
    )
    await db_session.commit()

    counts = await costing.backfill_invoice_links(db_session, org_id)
    assert counts == {"cost_center": 1, "department": 1, "project": 0}

    i1 = await db_session.scalar(select(Invoice).where(Invoice.invoice_number == "I-1"))
    i2 = await db_session.scalar(select(Invoice).where(Invoice.invoice_number == "I-2"))
    assert i1.department_id == dep.id and i1.cost_center_id is not None
    assert i2.cost_center_id is None  # unmatched tag invents nothing

    # Idempotent: a second run links nothing new.
    assert await costing.backfill_invoice_links(db_session, org_id) == {
        "cost_center": 0,
        "department": 0,
        "project": 0,
    }


@pytest.mark.asyncio
async def test_backfill_matches_name_when_code_misses(auth_client, db_session):
    org_id = await _org(db_session)
    vid = await _vendor(db_session, org_id)
    cc = await costing.create_cost_center(db_session, org_id, code="CC-100", name="Warehouse")
    db_session.add(_inv(org_id, vid, "N-1", cost_center="warehouse"))  # matches NAME, not code
    await db_session.commit()

    counts = await costing.backfill_invoice_links(db_session, org_id)
    assert counts["cost_center"] == 1
    i1 = await db_session.scalar(select(Invoice).where(Invoice.invoice_number == "N-1"))
    assert i1.cost_center_id == cc.id


@pytest.mark.asyncio
async def test_backfill_is_tenant_scoped(auth_client, db_session):
    org_a = await _org(db_session)
    org_b = Organization(name="Other Co")
    db_session.add(org_b)
    await db_session.flush()
    va, vb = await _vendor(db_session, org_a), await _vendor(db_session, org_b.id)
    await costing.create_cost_center(db_session, org_a, code="FLEET", name="Fleet A")
    await costing.create_cost_center(db_session, org_b.id, code="FLEET", name="Fleet B")
    db_session.add_all(
        [
            _inv(org_a, va, "A-1", cost_center="FLEET"),
            _inv(org_b.id, vb, "B-1", cost_center="FLEET"),
        ]
    )
    await db_session.commit()

    # Backfilling org A must not touch org B's invoice.
    await costing.backfill_invoice_links(db_session, org_a)
    b1 = await db_session.scalar(select(Invoice).where(Invoice.invoice_number == "B-1"))
    assert b1.cost_center_id is None


@pytest.mark.asyncio
async def test_backfill_runs_via_job_handler(auth_client, db_session):
    from app.services import job_handlers, jobs

    org_id = await _org(db_session)
    vid = await _vendor(db_session, org_id)
    await costing.create_cost_center(db_session, org_id, code="FLEET", name="Fleet")
    db_session.add(_inv(org_id, vid, "J-1", cost_center="FLEET"))
    await db_session.commit()

    await jobs.enqueue(db_session, job_handlers.COSTING_BACKFILL, org_id=org_id)
    job = await jobs.run_once(db_session, "w")
    assert job is not None and job.status == "succeeded"

    i = await db_session.scalar(select(Invoice).where(Invoice.invoice_number == "J-1"))
    assert i.cost_center_id is not None
