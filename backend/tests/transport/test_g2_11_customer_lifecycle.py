"""G2.11 (WO-73) — the customer lifecycle + per-country activation state
machine (`BA_fleet_fuel.md` §3.F F1/F3, §4's state tables, R44). The edge
set is EXACTLY the harvested one: (none)→prospect (add_prospect,
idempotent, never downgrades), prospect→pending (promote_prospect),
pending↔active (set_activation), active→inactive (set_inactive; terminal
in this slice); country: (none)→requested (request_country, idempotent,
never downgrades) and requested↔active (set_country_activation)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.customer_lifecycle import VatCustomerLifecycle
from app.services.transport import customer_lifecycle as lc
from tests.transport.conftest import enable_transport, make_entity, make_org

LIFECYCLE_ACTION = "transport.customer_lifecycle_set"
COUNTRY_ACTION = "transport.country_activation_set"


async def _setup(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    return org, entity


async def _events(db_session, org_id, action):
    return (
        await db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.org_id == org_id, AuditEvent.action == action)
            .order_by(AuditEvent.seq)
        )
    ).all()


@pytest.mark.asyncio
async def test_g2_11_add_prospect_creates_and_second_call_is_a_no_op(db_session):
    org, entity = await _setup(db_session)

    first = await lc.add_prospect(db_session, org.id, entity.id)
    await db_session.commit()
    assert first.status == "prospect"

    second = await lc.add_prospect(db_session, org.id, entity.id)
    await db_session.commit()
    assert second.id == first.id
    assert second.status == "prospect"

    rows = (
        await db_session.scalars(
            select(VatCustomerLifecycle).where(VatCustomerLifecycle.org_id == org.id)
        )
    ).all()
    assert len(rows) == 1
    # Exactly ONE audit event — the idempotent repeat audits nothing.
    assert len(await _events(db_session, org.id, LIFECYCLE_ACTION)) == 1


@pytest.mark.asyncio
async def test_g2_11_add_prospect_never_downgrades_a_real_client(db_session):
    """F1's own acceptance: a real client of ANY status is returned
    UNCHANGED by add_prospect — pending, active and inactive all keep
    their state."""
    org, entity = await _setup(db_session)
    await lc.add_prospect(db_session, org.id, entity.id)
    await lc.promote_prospect(db_session, org.id, entity.id)
    await db_session.commit()

    row = await lc.add_prospect(db_session, org.id, entity.id)
    assert row.status == "pending"

    await lc.set_activation(db_session, org.id, entity.id, True)
    await db_session.commit()
    row = await lc.add_prospect(db_session, org.id, entity.id)
    assert row.status == "active"

    await lc.set_inactive(db_session, org.id, entity.id)
    await db_session.commit()
    row = await lc.add_prospect(db_session, org.id, entity.id)
    assert row.status == "inactive"


@pytest.mark.asyncio
async def test_g2_11_promote_prospect_moves_to_pending_and_refuses_non_prospects(db_session):
    org, entity = await _setup(db_session)
    await lc.add_prospect(db_session, org.id, entity.id)
    await db_session.commit()

    row = await lc.promote_prospect(db_session, org.id, entity.id)
    await db_session.commit()
    assert row.status == "pending"

    with pytest.raises(AppError) as exc:
        await lc.promote_prospect(db_session, org.id, entity.id)
    assert exc.value.code == "not_a_prospect"
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_g2_11_set_activation_toggles_pending_and_active_only(db_session):
    """F1 verbatim: 'set_activation toggles pending ↔ active' — from
    prospect the handoff is not skippable, and inactive is terminal in
    this slice."""
    org, entity = await _setup(db_session)
    await lc.add_prospect(db_session, org.id, entity.id)
    await db_session.commit()

    # prospect → active directly: refused (promote first).
    with pytest.raises(AppError) as exc:
        await lc.set_activation(db_session, org.id, entity.id, True)
    assert exc.value.code == "lifecycle_transition_invalid"
    assert exc.value.status == 409

    await lc.promote_prospect(db_session, org.id, entity.id)
    row = await lc.set_activation(db_session, org.id, entity.id, True)
    assert row.status == "active"
    row = await lc.set_activation(db_session, org.id, entity.id, False)
    assert row.status == "pending"
    row = await lc.set_activation(db_session, org.id, entity.id, True)
    await db_session.commit()
    assert row.status == "active"

    # active → inactive is the churn edge; inactive is terminal.
    await lc.set_inactive(db_session, org.id, entity.id)
    await db_session.commit()
    with pytest.raises(AppError) as exc:
        await lc.set_activation(db_session, org.id, entity.id, True)
    assert exc.value.code == "lifecycle_transition_invalid"


@pytest.mark.asyncio
async def test_g2_11_set_inactive_only_from_active(db_session):
    org, entity = await _setup(db_session)
    await lc.add_prospect(db_session, org.id, entity.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lc.set_inactive(db_session, org.id, entity.id)
    assert exc.value.code == "lifecycle_transition_invalid"

    # An entity with NO lifecycle row at all is refused the same way.
    entity2 = await make_entity(db_session, org.id, seed=2)
    await db_session.commit()
    with pytest.raises(AppError) as exc:
        await lc.set_inactive(db_session, org.id, entity2.id)
    assert exc.value.code == "lifecycle_transition_invalid"


@pytest.mark.asyncio
async def test_g2_11_every_real_transition_is_audited_old_to_new(db_session):
    org, entity = await _setup(db_session)
    await lc.add_prospect(db_session, org.id, entity.id)
    await lc.promote_prospect(db_session, org.id, entity.id)
    await lc.set_activation(db_session, org.id, entity.id, True)
    await lc.set_inactive(db_session, org.id, entity.id)
    await db_session.commit()

    events = await _events(db_session, org.id, LIFECYCLE_ACTION)
    assert len(events) == 4
    assert events[0].meta is not None and '"old_status":null' in events[0].meta
    assert '"new_status":"prospect"' in events[0].meta
    assert events[1].meta is not None and '"old_status":"prospect"' in events[1].meta
    assert '"new_status":"pending"' in events[1].meta
    assert events[2].meta is not None and '"old_status":"pending"' in events[2].meta
    assert '"new_status":"active"' in events[2].meta
    assert events[3].meta is not None and '"old_status":"active"' in events[3].meta
    assert '"new_status":"inactive"' in events[3].meta


@pytest.mark.asyncio
async def test_g2_11_request_country_is_idempotent_and_never_downgrades(db_session):
    org, entity = await _setup(db_session)

    first = await lc.request_country(db_session, org.id, entity.id, "fr")
    await db_session.commit()
    assert first.status == "requested"
    assert first.country == "FR"  # normalised

    second = await lc.request_country(db_session, org.id, entity.id, "FR")
    assert second.id == first.id
    assert len(await _events(db_session, org.id, COUNTRY_ACTION)) == 1

    # Never downgrades: requesting an already-ACTIVE country changes nothing.
    await lc.set_country_activation(db_session, org.id, entity.id, "FR", True)
    await db_session.commit()
    row = await lc.request_country(db_session, org.id, entity.id, "FR")
    assert row.status == "active"


@pytest.mark.asyncio
async def test_g2_11_country_activation_ladder_is_linear(db_session):
    """§4's state table: (none) → requested → active — activating a
    never-requested country is refused; requested ↔ active toggles."""
    org, entity = await _setup(db_session)

    with pytest.raises(AppError) as exc:
        await lc.set_country_activation(db_session, org.id, entity.id, "FR", True)
    assert exc.value.code == "country_not_requested"
    assert exc.value.status == 409

    await lc.request_country(db_session, org.id, entity.id, "FR")
    row = await lc.set_country_activation(db_session, org.id, entity.id, "FR", True)
    assert row.status == "active"

    # Idempotent repeat of the same target state is refused (two edges only).
    with pytest.raises(AppError) as exc:
        await lc.set_country_activation(db_session, org.id, entity.id, "FR", True)
    assert exc.value.code == "country_transition_invalid"

    row = await lc.set_country_activation(db_session, org.id, entity.id, "FR", False)
    await db_session.commit()
    assert row.status == "requested"

    events = await _events(db_session, org.id, COUNTRY_ACTION)
    assert len(events) == 3  # request + activate + deactivate
    assert events[1].meta is not None and '"old_status":"requested"' in events[1].meta
    assert '"new_status":"active"' in events[1].meta


@pytest.mark.asyncio
async def test_g2_11_invalid_country_code_is_refused(db_session):
    org, entity = await _setup(db_session)
    with pytest.raises(AppError) as exc:
        await lc.request_country(db_session, org.id, entity.id, "FRA")
    assert exc.value.code == "invalid_country"
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_g2_11_cross_tenant_entity_is_an_opaque_404(db_session):
    """Org B's entity id through org A's service — indistinguishable from
    an unknown id (master-context §4.4: 404, never 403)."""
    org_a, _ = await _setup(db_session)
    org_b = await make_org(db_session, name="Other Org")
    await enable_transport(db_session, org_b.id)
    entity_b = await make_entity(db_session, org_b.id, seed=9)
    await db_session.commit()

    for call in (
        lc.add_prospect(db_session, org_a.id, entity_b.id),
        lc.promote_prospect(db_session, org_a.id, entity_b.id),
        lc.request_country(db_session, org_a.id, entity_b.id, "FR"),
    ):
        with pytest.raises(AppError) as exc:
            await call
        assert exc.value.code == "entity_not_found"
        assert exc.value.status == 404


@pytest.mark.asyncio
async def test_g2_11_module_disabled_refuses(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lc.add_prospect(db_session, org.id, entity.id)
    assert exc.value.code == "module_not_enabled"


@pytest.mark.asyncio
async def test_g2_11_db_check_refuses_a_bogus_status(db_session):
    """Defense-in-depth: the CHECK constraint refuses a raw insert outside
    the four F1 states even if a future code path bypassed the service."""
    import sqlalchemy.exc

    org, entity = await _setup(db_session)
    db_session.add(VatCustomerLifecycle(org_id=org.id, entity_id=entity.id, status="bogus"))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await db_session.flush()
    await db_session.rollback()
