"""G3.5 (WO-72) — supplier invoicing cadence configuration
(`BA_fleet_fuel.md` §3.J item 1: the three-value closed set; §5.1: the
harvested per-network assignments). Admin row beats the harvested
default; a supplier with neither resolves to no cadence at all (opt-in
by absence — the R61/WO-66 discipline)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.receipt_control import VatSupplierCadence
from app.services.transport import receipt_control
from tests.transport.conftest import enable_transport, make_org


@pytest.mark.asyncio
async def test_g3_5_set_cadence_creates_and_is_idempotent(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await db_session.commit()

    first = await receipt_control.set_cadence(
        db_session, org.id, supplier="CASHCO", cadence="monthly"
    )
    await db_session.commit()
    second = await receipt_control.set_cadence(
        db_session, org.id, supplier="CASHCO", cadence="monthly"
    )
    await db_session.commit()
    assert first.id == second.id

    rows = (
        await db_session.scalars(
            select(VatSupplierCadence).where(VatSupplierCadence.org_id == org.id)
        )
    ).all()
    assert len(rows) == 1

    # Exactly ONE audit event — the idempotent repeat audits nothing
    # (the checklist.set_active no-op convention).
    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.supplier_cadence_set",
            )
        )
    ).all()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_g3_5_set_cadence_change_audits_old_to_new(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await db_session.commit()

    await receipt_control.set_cadence(db_session, org.id, supplier="CASHCO", cadence="monthly")
    await db_session.commit()
    await receipt_control.set_cadence(db_session, org.id, supplier="CASHCO", cadence="semi-monthly")
    await db_session.commit()

    events = (
        await db_session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.supplier_cadence_set",
            )
            .order_by(AuditEvent.seq)
        )
    ).all()
    assert len(events) == 2
    assert events[-1].meta is not None
    assert '"old_cadence":"monthly"' in events[-1].meta
    assert '"new_cadence":"semi-monthly"' in events[-1].meta


@pytest.mark.asyncio
async def test_g3_5_set_cadence_refuses_a_value_outside_the_harvested_set(db_session):
    """§3.J item 1 is a CLOSED set — `weekly` is not in it."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await receipt_control.set_cadence(db_session, org.id, supplier="E100", cadence="weekly")
    assert exc.value.code == "invalid_cadence"
    assert exc.value.status == 422

    rows = (
        await db_session.scalars(
            select(VatSupplierCadence).where(VatSupplierCadence.org_id == org.id)
        )
    ).all()
    assert rows == []


@pytest.mark.asyncio
async def test_g3_5_remove_cadence_deletes_and_is_a_no_op_the_second_time(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await db_session.commit()
    await receipt_control.set_cadence(db_session, org.id, supplier="CASHCO", cadence="monthly")
    await db_session.commit()

    assert await receipt_control.remove_cadence(db_session, org.id, supplier="CASHCO") is True
    await db_session.commit()
    assert await receipt_control.remove_cadence(db_session, org.id, supplier="CASHCO") is False

    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.supplier_cadence_remove",
            )
        )
    ).all()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_g3_5_cadence_for_admin_row_beats_the_harvested_default(db_session):
    """E100's harvested default is semi-monthly (§5.1); an explicit admin
    assignment overrides it."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await db_session.commit()

    assert await receipt_control.cadence_for(db_session, org.id, "E100") == "semi-monthly"
    await receipt_control.set_cadence(db_session, org.id, supplier="E100", cadence="monthly")
    await db_session.commit()
    assert await receipt_control.cadence_for(db_session, org.id, "E100") == "monthly"


@pytest.mark.asyncio
async def test_g3_5_harvested_defaults_match_section_5_1(db_session):
    """§3.J item 1 / §5.1 verbatim: semi-monthly (E100, DKV) · monthly
    (MOEVE, BP, TFC, PORTONE) · monthly-per-country (Q8). Matched
    case-insensitively against the G3.2 parser network codes (`Moeve`);
    Eurowag ("—" in §5.1) and an unknown code resolve to None."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await db_session.commit()

    expected = {
        "E100": "semi-monthly",
        "DKV": "semi-monthly",
        "MOEVE": "monthly",
        "Moeve": "monthly",  # the parser's own casing
        "BP": "monthly",
        "TFC": "monthly",
        "PORTONE": "monthly",
        "Q8": "monthly-per-country",
    }
    for supplier, cadence in expected.items():
        assert await receipt_control.cadence_for(db_session, org.id, supplier) == cadence

    assert await receipt_control.cadence_for(db_session, org.id, "Eurowag") is None
    assert await receipt_control.cadence_for(db_session, org.id, "UNKNOWNCO") is None


@pytest.mark.asyncio
async def test_g3_5_cadence_services_gate_on_the_transport_module(db_session):
    """Module OFF ⇒ every entry point refuses (the invariant pattern of
    every transport service)."""
    org = await make_org(db_session)  # transport NOT enabled
    await db_session.commit()

    for coro in (
        receipt_control.set_cadence(db_session, org.id, supplier="E100", cadence="monthly"),
        receipt_control.remove_cadence(db_session, org.id, supplier="E100"),
        receipt_control.list_cadences(db_session, org.id),
        receipt_control.run_receipt_control(db_session, org.id, "2026-05"),
        receipt_control.list_controls(db_session, org.id, "2026-05"),
        receipt_control.orphan_transactions(db_session, org.id, "2026-05"),
    ):
        with pytest.raises(AppError) as exc:
            await coro
        assert exc.value.code == "module_not_enabled"
