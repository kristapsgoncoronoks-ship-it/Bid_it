"""G3.3 (WO-66) — the engine tie-out halting the monthly close (R25's
second acceptance line: "line count off by one ⇒ close halts"), proven
through the REAL job path (`enqueue_close` + `jobs.run_once`) so the
"hand-off artifact is not written" translation is literal: the failed
run's whole session rolls back and NO claim-line rebuild survives."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.models.audit import AuditEvent
from app.models.job import DEAD
from app.models.transport.vat_claim import VatRefundClaimLine
from app.services import jobs
from app.services.transport import claim as claim_svc
from app.services.transport import close, fuel_ingest, tie_out
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _make_claim(db_session, org, entity, **overrides):
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_txn(db_session, org, entity, *, supplier="Q8", line_seq=1, period="2026-05"):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 15),
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=Decimal("100.00"),
        vat_local=Decimal("21.00"),
        gross_local=Decimal("121.00"),
        net_eur=Decimal("100.00"),
        vat_eur=Decimal("21.00"),
        invoice_ref="INV-0001",
    )


async def _set_expectation(db_session, org, entity, *, supplier="Q8", lines=1, **overrides):
    kwargs = dict(
        entity_id=entity.id,
        supplier=supplier,
        period="2026-05",
        currency="EUR",
        expected_lines=lines,
    )
    kwargs.update(overrides)
    return await tie_out.set_expectation(db_session, org.id, **kwargs)


@pytest.mark.asyncio
async def test_g3_3_line_count_off_by_one_halts_the_close(db_session):
    """R25 verbatim, inline: one transaction captured, two expected —
    `run_close` raises `tie_out_failed` BEFORE any claim work."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await _set_expectation(db_session, org, entity, lines=2)  # off by one
    await db_session.commit()

    with pytest.raises(ValidationError) as exc_info:
        await close.run_close(db_session, org.id, "2026-05")
    assert exc_info.value.code == "tie_out_failed"
    assert "Q8/EUR lines: expected 2, got 1" in str(exc_info.value)


@pytest.mark.asyncio
async def test_g3_3_failed_tie_out_through_the_job_worker_writes_no_claim_lines(db_session):
    """The 'hand-off artifact is not written' proof, through the REAL
    worker path: the job fails (retried or dead-lettered, never
    succeeded), and the draft claim's lines were NOT rebuilt."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    claim_id = claim.id
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await _set_expectation(db_session, org, entity, lines=2)  # off by one
    await db_session.commit()

    await close.enqueue_close(db_session, org.id, "2026-05")
    job = await jobs.run_once(db_session, "test-worker")

    assert job is not None
    assert job.status in ("queued", DEAD)  # never succeeded
    assert "tie-out failed" in (job.last_error or "").lower() or "tie_out" in (job.last_error or "")

    lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim_id)
        )
    ).all()
    assert lines == [], "a failed tie-out must leave the claim's lines untouched"


@pytest.mark.asyncio
async def test_g3_3_all_failing_suppliers_are_named_in_one_error(db_session):
    """The harvested 'exit AFTER processing every supplier so the operator
    sees all failures at once': two suppliers both off ⇒ ONE error naming
    both."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(db_session, org, entity, supplier="Q8", line_seq=1)
    await _make_txn(db_session, org, entity, supplier="DKV", line_seq=1)
    await _set_expectation(db_session, org, entity, supplier="Q8", lines=5)
    await _set_expectation(db_session, org, entity, supplier="DKV", lines=7)
    await db_session.commit()

    with pytest.raises(ValidationError) as exc_info:
        await close.run_close(db_session, org.id, "2026-05")
    message = str(exc_info.value)
    assert "Q8/EUR lines: expected 5, got 1" in message
    assert "DKV/EUR lines: expected 7, got 1" in message


@pytest.mark.asyncio
async def test_g3_3_passing_tie_out_closes_and_audits_the_check_count(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await _set_expectation(
        db_session,
        org,
        entity,
        lines=1,
        expected_gross_local=Decimal("121.00"),
        expected_net_eur=Decimal("100.00"),
        expected_gross_eur=Decimal("121.00"),
        expected_diesel_litres=Decimal("100.000"),
    )
    await db_session.commit()

    result = await close.run_close(db_session, org.id, "2026-05")
    await db_session.commit()

    assert result["tie_out_checked"] == 1
    assert result["claims_processed"] == 1
    lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert len(lines) == 1

    event = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id, AuditEvent.action == "transport.close_run"
        )
    )
    assert event is not None
    meta = json.loads(event.meta) if isinstance(event.meta, str) else event.meta
    assert meta["tie_out_checked"] == 1


@pytest.mark.asyncio
async def test_g3_3_close_without_expectations_is_unchanged(db_session):
    """Fail-open by absence: an org that never typed an expectation closes
    exactly as WO-53 shipped it (tie_out_checked = 0, nothing halted)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await db_session.commit()

    result = await close.run_close(db_session, org.id, "2026-05")
    assert result["tie_out_checked"] == 0
    assert result["claims_processed"] == 1


@pytest.mark.asyncio
async def test_g3_3_removing_the_expectation_unhalts_the_close(db_session):
    """The narrow mitigation the rollback strategy names: deleting a
    mis-typed expectation returns that key to never-typed (fail-open)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(db_session, org, entity)
    await _set_expectation(db_session, org, entity, lines=99)  # a typo
    await db_session.commit()

    with pytest.raises(ValidationError):
        await close.run_close(db_session, org.id, "2026-05")

    await tie_out.remove_expectation(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        period="2026-05",
        currency="EUR",
    )
    await db_session.commit()

    result = await close.run_close(db_session, org.id, "2026-05")
    assert result["tie_out_checked"] == 0
