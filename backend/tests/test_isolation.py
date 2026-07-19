"""Defence-in-depth: the ORM tenant guard scopes even queries that forget to
filter by org_id."""
from datetime import date

import pytest
from sqlalchemy import select

from app.core.tenant import reset_current_org, set_current_org
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.vendor import Vendor


async def _seed_two_tenants(db):
    a = Organization(name="Tenant A")
    b = Organization(name="Tenant B")
    db.add_all([a, b])
    await db.flush()
    va = Vendor(org_id=a.id, name="VendorA")
    vb = Vendor(org_id=b.id, name="VendorB")
    db.add_all([va, vb])
    await db.flush()
    db.add(Invoice(org_id=a.id, vendor_id=va.id, invoice_number="A-1", issue_date=date(2026, 1, 1)))
    db.add(Invoice(org_id=b.id, vendor_id=vb.id, invoice_number="B-1", issue_date=date(2026, 1, 1)))
    await db.commit()
    return a, b


@pytest.mark.asyncio
async def test_unfiltered_select_is_tenant_scoped(db_session):
    a, b = await _seed_two_tenants(db_session)

    # Unscoped context sees everything (bootstrap / operator path).
    assert len((await db_session.scalars(select(Invoice))).all()) == 2

    # Scoped to A: an UNFILTERED select returns only A's rows.
    token = set_current_org(a.id)
    try:
        invs = (await db_session.scalars(select(Invoice))).all()
        assert {i.invoice_number for i in invs} == {"A-1"}
        vendors = (await db_session.scalars(select(Vendor))).all()
        assert {v.name for v in vendors} == {"VendorA"}
    finally:
        reset_current_org(token)

    # Scope restored to unscoped.
    assert len((await db_session.scalars(select(Invoice))).all()) == 2


@pytest.mark.asyncio
async def test_scope_blocks_cross_tenant_fetch_by_id(db_session):
    a, b = await _seed_two_tenants(db_session)
    b_invoice = (await db_session.scalars(
        select(Invoice).where(Invoice.invoice_number == "B-1")
    )).one()

    # As tenant A, B's invoice is invisible even fetched by primary key.
    token = set_current_org(a.id)
    try:
        found = (await db_session.scalars(select(Invoice).where(Invoice.id == b_invoice.id))).first()
        assert found is None
    finally:
        reset_current_org(token)
