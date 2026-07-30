"""G3.1 slice 1 — the per-country supplier legal-entity registry (R21/R22),
`BA_fleet_fuel.md` §3 (Eurowag per-country footer / E100 anchor context).
See `app.services.transport.supplier_entity`'s module docstring for why
R20 (actually reading the seller off a document) is explicitly NOT closed
by this order — deferred to `G3.2`, the fuel-card parser registry.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.transport.supplier_registration import SupplierVatRegistration
from app.models.vendor import Vendor
from app.services.transport import supplier_entity
from tests.factories.transport import synthetic_vat_id
from tests.transport.conftest import enable_transport, make_org


@pytest.mark.asyncio
async def test_g3_1_get_registration_returns_none_when_unregistered(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)

    result = await supplier_entity.get_registration(
        db_session, org.id, supplier="Eurowag", country="BE"
    )
    assert result is None


@pytest.mark.asyncio
async def test_g3_1_set_registration_creates_an_admin_row(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)

    row = await supplier_entity.set_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=synthetic_vat_id("BE"),
        entity_name="Eurowag Belgium BVBA",
    )
    await db_session.commit()
    assert row.source == "admin"
    assert row.entity_name == "Eurowag Belgium BVBA"

    fetched = await supplier_entity.get_registration(
        db_session, org.id, supplier="Eurowag", country="BE"
    )
    assert fetched is not None
    assert fetched.id == row.id


@pytest.mark.asyncio
async def test_g3_1_set_registration_updates_an_existing_admin_row(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    first_vat = synthetic_vat_id("BE", seed=1)
    second_vat = synthetic_vat_id("BE", seed=2)

    await supplier_entity.set_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=first_vat,
        entity_name="Eurowag Belgium BVBA",
    )
    await db_session.commit()

    corrected = await supplier_entity.set_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=second_vat,
        entity_name="Eurowag Belgium BVBA (corrected)",
    )
    await db_session.commit()
    assert corrected.vat_number == second_vat
    assert corrected.entity_name == "Eurowag Belgium BVBA (corrected)"
    assert corrected.source == "admin"

    rows = (
        await db_session.scalars(
            select(SupplierVatRegistration).where(SupplierVatRegistration.org_id == org.id)
        )
    ).all()
    assert len(rows) == 1, "an admin correction must UPDATE, never duplicate"


@pytest.mark.asyncio
async def test_g3_1_learn_registration_seeds_a_capture_row_when_none_exists(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)

    row = await supplier_entity.learn_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=synthetic_vat_id("BE"),
        entity_name="Eurowag Belgium BVBA",
    )
    await db_session.commit()
    assert row.source == "capture"


@pytest.mark.asyncio
async def test_g3_1_learn_registration_never_overwrites_an_existing_admin_row(db_session):
    """R22 verbatim: confirming a statement whose supply country seeds that
    country's registration, NEVER clobbering a curated value."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    curated_vat = synthetic_vat_id("BE", seed=1)
    await supplier_entity.set_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=curated_vat,
        entity_name="Eurowag Belgium BVBA (curated)",
    )
    await db_session.commit()

    learned = await supplier_entity.learn_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=synthetic_vat_id("BE", seed=2),
        entity_name="Some Other Captured Name",
    )
    await db_session.commit()

    assert learned.vat_number == curated_vat
    assert learned.entity_name == "Eurowag Belgium BVBA (curated)"
    assert learned.source == "admin"


@pytest.mark.asyncio
async def test_g3_1_learn_registration_never_overwrites_an_existing_capture_row(db_session):
    """A second, possibly-conflicting capture never silently wins over an
    earlier one — learning is insert-only, never update."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    first_vat = synthetic_vat_id("BE", seed=1)
    await supplier_entity.learn_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=first_vat,
        entity_name="Eurowag Belgium BVBA",
    )
    await db_session.commit()

    second = await supplier_entity.learn_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=synthetic_vat_id("BE", seed=2),
        entity_name="A Conflicting Capture",
    )
    await db_session.commit()

    assert second.vat_number == first_vat
    assert second.entity_name == "Eurowag Belgium BVBA"
    assert second.source == "capture"


@pytest.mark.asyncio
async def test_g3_1_set_registration_overwrites_an_existing_capture_row(db_session):
    """Curated always wins over learned, in either order."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await supplier_entity.learn_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=synthetic_vat_id("BE", seed=1),
        entity_name="Learned Name",
    )
    await db_session.commit()

    admin_vat = synthetic_vat_id("BE", seed=2)
    curated = await supplier_entity.set_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=admin_vat,
        entity_name="Eurowag Belgium BVBA (curated)",
    )
    await db_session.commit()
    assert curated.source == "admin"
    assert curated.vat_number == admin_vat


@pytest.mark.asyncio
async def test_g3_1_learning_a_different_country_never_touches_the_first_or_any_vendor(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await supplier_entity.set_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="CZ",
        vat_number=synthetic_vat_id("CZ"),
        entity_name="W.A.G. Issuing Services, a.s.",
    )
    await db_session.commit()

    await supplier_entity.learn_registration(
        db_session,
        org.id,
        supplier="Eurowag",
        country="BE",
        vat_number=synthetic_vat_id("BE"),
        entity_name="Eurowag Belgium BVBA",
    )
    await db_session.commit()

    cz = await supplier_entity.get_registration(
        db_session, org.id, supplier="Eurowag", country="CZ"
    )
    assert cz is not None
    assert cz.entity_name == "W.A.G. Issuing Services, a.s."
    assert cz.source == "admin"

    vendors = (await db_session.scalars(select(Vendor).where(Vendor.org_id == org.id))).all()
    assert vendors == [], "learning must never create or touch a Vendor row"


@pytest.mark.asyncio
async def test_g3_1_cross_tenant_isolation_with_an_identical_key(db_session):
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)

    await supplier_entity.set_registration(
        db_session,
        org_a.id,
        supplier="Eurowag",
        country="BE",
        vat_number=synthetic_vat_id("BE", seed=1),
        entity_name="Org A's Eurowag Belgium BVBA",
    )
    await db_session.commit()

    result = await supplier_entity.get_registration(
        db_session, org_b.id, supplier="Eurowag", country="BE"
    )
    assert result is None, "an identical (supplier, country) key in another org must be invisible"
