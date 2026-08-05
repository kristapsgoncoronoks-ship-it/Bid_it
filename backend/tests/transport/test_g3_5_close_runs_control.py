"""G3.5 (WO-72) — the `run_control` stage inside the monthly close
(`BA_fleet_fuel.md` §3.H H4's harvested stage list
`consolidate → build_master → history → run_control → backup`): the close
PERSISTS the period's receipt-control grid and completes regardless of
what the control FINDS (§4.19 — findings never halt); the R25 tie-out
gate still halts BEFORE the control stage runs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import ValidationError
from app.models.audit import AuditEvent
from app.models.transport.receipt_control import VatReceiptControl
from app.services.transport import claim as claim_svc
from app.services.transport import close, fuel_ingest, tie_out
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

PERIOD = "2026-05"


async def _make_txn(db_session, org, entity, *, supplier="E100", line_seq=1):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier=supplier,
        period=PERIOD,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 10),
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=Decimal("100.00"),
        vat_local=Decimal("21.00"),
        gross_local=Decimal("121.00"),
        net_eur=Decimal("100.00"),
        vat_eur=Decimal("21.00"),
        invoice_ref=None,
    )


async def _control_rows(db_session, org_id: str) -> int:
    return (
        await db_session.scalar(
            select(func.count())
            .select_from(VatReceiptControl)
            .where(VatReceiptControl.org_id == org_id)
        )
    ) or 0


@pytest.mark.asyncio
async def test_g3_5_close_persists_the_control_grid_and_reports_the_summary(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2026-Q2"
    )
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await db_session.commit()

    result = await close.run_close(db_session, org.id, PERIOD)
    await db_session.commit()

    # E100 is semi-monthly: H1 (missing) + H2 (no_activity) persisted.
    assert result["receipt_control"]["rows"] == 2
    assert result["receipt_control"]["missing"] == 1
    assert await _control_rows(db_session, org.id) == 2

    event = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id, AuditEvent.action == "transport.close_run"
        )
    )
    assert event is not None and event.meta is not None
    assert '"receipt_control"' in event.meta


@pytest.mark.asyncio
async def test_g3_5_missing_findings_never_halt_the_close(db_session):
    """§3.J's verbs are 'answers'/'persist'/'chase' — a `missing` slot is
    a worklist row, never a halt (the deliberate contrast with the
    tie-out's harvested 'halts')."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await db_session.commit()

    result = await close.run_close(db_session, org.id, PERIOD)  # does not raise
    assert result["receipt_control"]["missing"] == 1
    assert result["period"] == PERIOD


@pytest.mark.asyncio
async def test_g3_5_tie_out_failure_halts_before_the_control_stage(db_session):
    """Stage order preserved: the R25 tie-out gate raises FIRST, so a
    failed close persists ZERO control rows (nothing partially applied,
    R31)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await tie_out.set_expectation(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="E100",
        period=PERIOD,
        currency="EUR",
        expected_lines=2,  # off by one — the tie-out fails
    )
    await db_session.commit()
    org_id = org.id  # capture before the rollback expires the instance

    with pytest.raises(ValidationError) as exc:
        await close.run_close(db_session, org_id, PERIOD)
    assert exc.value.code == "tie_out_failed"
    await db_session.rollback()
    assert await _control_rows(db_session, org_id) == 0
