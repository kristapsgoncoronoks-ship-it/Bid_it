"""WO-AG — F3's `country_requirements` + `country_ready_to_activate`.

`BA_fleet_fuel.md` §3.F F3: country activation is per (customer, refund
country) "with its own required-document set (`country_requirements`, default
`["power_of_attorney"]`)"; `country_ready_to_activate` is INFORMATIONAL ONLY —
it does not activate and is not a gate. Deferred at WO-73 until the
customer-document store existed; WO-AB shipped it, so the helper is real.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import AppError
from app.services.transport import claimant_documents, customer_lifecycle
from tests.transport.conftest import enable_transport, make_entity, make_org

TODAY = date(2026, 9, 6)


async def _doc(
    db_session, org, entity, *, kind: str, country: str | None, valid_until: date | None
):
    return await claimant_documents.record(
        db_session,
        org.id,
        entity.id,
        kind=kind,
        sha256=f"{kind}{country or ''}".ljust(64, "0")[:64],
        size=10,
        country=country,
        valid_until=valid_until,
    )


@pytest.mark.asyncio
async def test_wo_ag_default_set_is_a_power_of_attorney_and_absence_reads_missing(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, documents=False)
    await db_session.commit()

    assert await customer_lifecycle.country_requirements(db_session, org.id, "lv") == (
        "power_of_attorney",
    )
    r = await customer_lifecycle.country_ready_to_activate(
        db_session, org.id, entity.id, "LV", today=TODAY
    )
    assert r.ready is False and r.is_default is True
    assert r.required == ("power_of_attorney",)
    assert r.missing == ("power_of_attorney",) and r.expired == ()


@pytest.mark.asyncio
async def test_wo_ag_a_valid_document_for_the_customer_or_the_country_makes_it_ready(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, documents=False)
    await db_session.commit()

    # An EXPIRED country-specific PoA: on file, not valid — "expired", not "missing".
    await _doc(
        db_session,
        org,
        entity,
        kind="power_of_attorney",
        country="LV",
        valid_until=date(2026, 8, 1),
    )
    await db_session.commit()
    r = await customer_lifecycle.country_ready_to_activate(
        db_session, org.id, entity.id, "LV", today=TODAY
    )
    assert r.ready is False and r.expired == ("power_of_attorney",) and r.missing == ()

    # A customer-wide PoA (no country) still valid covers the country.
    await _doc(db_session, org, entity, kind="power_of_attorney", country=None, valid_until=None)
    await db_session.commit()
    r = await customer_lifecycle.country_ready_to_activate(
        db_session, org.id, entity.id, "LV", today=TODAY
    )
    assert r.ready is True and r.missing == () and r.expired == ()


@pytest.mark.asyncio
async def test_wo_ag_a_configured_set_replaces_the_default_and_an_empty_set_restores_it(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, documents=False)
    await db_session.commit()

    kinds = await customer_lifecycle.set_country_requirements(
        db_session, org.id, "PL", ["power_of_attorney", "vat_certificate", "power_of_attorney"]
    )
    await db_session.commit()
    assert kinds == ("power_of_attorney", "vat_certificate")  # de-duplicated, order kept
    assert await customer_lifecycle.list_country_requirements(db_session, org.id) == {
        "PL": ("power_of_attorney", "vat_certificate")
    }

    await _doc(db_session, org, entity, kind="power_of_attorney", country="PL", valid_until=None)
    await db_session.commit()
    r = await customer_lifecycle.country_ready_to_activate(
        db_session, org.id, entity.id, "PL", today=TODAY
    )
    assert r.is_default is False and r.ready is False
    assert r.missing == ("vat_certificate",)

    # Another country is untouched by PL's configuration.
    other = await customer_lifecycle.country_ready_to_activate(
        db_session, org.id, entity.id, "LT", today=TODAY
    )
    assert other.is_default is True and other.required == ("power_of_attorney",)

    # Empty → back to the default; the configuration is gone, not "nothing required".
    restored = await customer_lifecycle.set_country_requirements(db_session, org.id, "PL", [])
    await db_session.commit()
    assert restored == ("power_of_attorney",)
    assert await customer_lifecycle.list_country_requirements(db_session, org.id) == {}

    with pytest.raises(AppError) as refused:
        await customer_lifecycle.set_country_requirements(db_session, org.id, "PL", ["passport"])
    assert refused.value.code == "invalid_document_kind"


@pytest.mark.asyncio
async def test_wo_ag_readiness_is_informational_the_activation_gate_ignores_it(db_session):
    """F3, verbatim: 'it does not activate and is not a gate'. A country with
    nothing on file still activates on the explicit click, and a ready one
    is not activated by being ready."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, documents=False)
    await db_session.commit()
    await customer_lifecycle.add_prospect(db_session, org.id, entity.id)
    await customer_lifecycle.promote_prospect(db_session, org.id, entity.id)
    await customer_lifecycle.request_country(db_session, org.id, entity.id, "LV")
    await db_session.commit()

    r = await customer_lifecycle.country_ready_to_activate(
        db_session, org.id, entity.id, "LV", today=TODAY
    )
    assert r.ready is False
    row = await customer_lifecycle.get_country_activation(db_session, org.id, entity.id, "LV")
    assert row is not None and row.status == "requested"  # readiness moved nothing

    activated = await customer_lifecycle.set_country_activation(
        db_session, org.id, entity.id, "LV", active=True
    )
    assert activated.status == "active"  # the click is not refused on readiness


@pytest.mark.asyncio
async def test_wo_ag_over_http_the_lifecycle_carries_readiness_and_the_admin_sets_the_requirements(
    auth_client, db_session
):
    from sqlalchemy import select

    from app.models.organization import Organization
    from app.models.user import User

    user = await db_session.scalar(select(User).order_by(User.created_at).limit(1))
    org = await db_session.get(Organization, user.org_id)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, documents=False)
    await db_session.commit()

    base = f"/api/v1/transport/customers/{entity.id}"
    assert (await auth_client.post(f"{base}/prospect")).status_code == 200
    assert (await auth_client.post(f"{base}/countries/LV/request")).status_code == 200

    life = (await auth_client.get(f"{base}/lifecycle")).json()
    (lv,) = life["countries"]
    assert lv["readiness"] == {
        "ready": False,
        "required": ["power_of_attorney"],
        "missing": ["power_of_attorney"],
        "expired": [],
        "is_default": True,
    }

    assert (await auth_client.get("/api/v1/transport/country-requirements")).json() == []
    put = await auth_client.put(
        "/api/v1/transport/country-requirements/lv", json={"kinds": ["vat_certificate"]}
    )
    assert put.status_code == 200, put.text
    assert put.json() == {"country": "LV", "kinds": ["vat_certificate"]}
    assert (await auth_client.get("/api/v1/transport/country-requirements")).json() == [
        {"country": "LV", "kinds": ["vat_certificate"]}
    ]
    bad = await auth_client.put(
        "/api/v1/transport/country-requirements/LV", json={"kinds": ["passport"]}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_document_kind"

    life = (await auth_client.get(f"{base}/lifecycle")).json()
    assert life["countries"][0]["readiness"]["required"] == ["vat_certificate"]
    assert life["countries"][0]["readiness"]["is_default"] is False
