"""G1.1 (no R-requirement — ADR-P3 rule 3 / VAT_HARVEST E.2 §3): the transport
vertical is an entitlement, default OFF, plan-gated exactly like `issuing`/
`expenses`. Proves the module is INVISIBLE/INERT when the entitlement is off:
disabled by default for a fresh org, absent from every plan's module set
(pricing is an owner-blocked commercial decision — `docs/DECISIONS-NEEDED.md`
§10), and the one reachable service entry point (`get_or_create_claim`)
refuses with 403 until the module is switched on.
"""

from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.services import modules, plans
from app.services.transport import claim
from tests.transport.conftest import enable_transport, make_entity, make_org


def test_transport_module_is_registered_core_false_default_off():
    m = modules.MODULES_BY_KEY["transport"]
    assert m.core is False
    assert m.default is False


def test_transport_absent_from_every_plan_today():
    """A pricing decision, deliberately not made in code yet
    (docs/DECISIONS-NEEDED.md §10) — every plan's module set excludes
    'transport' so PUT /modules/transport 402s everywhere until it is priced."""
    for plan_key in plans.PLANS:
        assert not plans.allows_module(plan_key, "transport"), plan_key


@pytest.mark.asyncio
async def test_transport_disabled_by_default_for_a_fresh_org(db_session):
    org = await make_org(db_session)
    assert await modules.is_enabled(db_session, org.id, "transport") is False
    assert "transport" not in await modules.enabled_keys(db_session, org.id)


@pytest.mark.asyncio
async def test_transport_enabled_after_explicit_set_enabled(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    assert await modules.is_enabled(db_session, org.id, "transport") is True
    assert "transport" in await modules.enabled_keys(db_session, org.id)


@pytest.mark.asyncio
async def test_transport_service_is_inert_when_the_module_is_off(db_session):
    """The one reachable entry point into the transport tables today refuses
    BEFORE any query when the entitlement is off — proven by the fact that it
    still refuses even for an entity that does not exist (if it queried first,
    a NotFoundError would fire instead of the module 403)."""
    org = await make_org(db_session)
    # Deliberately no enable_transport() call — module stays OFF (the default).

    with pytest.raises(AppError) as exc:
        await claim.get_or_create_claim(
            db_session,
            org.id,
            entity_id="00000000-0000-0000-0000-000000000000",
            refund_country="LV",
            ref_period="2025-Q4",
        )
    assert exc.value.status == 403
    assert exc.value.code == "module_not_enabled"


@pytest.mark.asyncio
async def test_transport_service_works_once_the_module_is_on(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    c = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2025-Q4"
    )
    assert c.org_id == org.id


@pytest.mark.asyncio
async def test_transport_off_for_one_org_does_not_leak_from_another_orgs_activation(db_session):
    """Entitlement is per-org, not global — activating it for one tenant must
    not switch it on for a sibling tenant."""
    org_on = await make_org(db_session, name="On")
    org_off = await make_org(db_session, name="Off")
    await enable_transport(db_session, org_on.id)

    assert await modules.is_enabled(db_session, org_on.id, "transport") is True
    assert await modules.is_enabled(db_session, org_off.id, "transport") is False
