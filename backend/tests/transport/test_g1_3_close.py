"""G1.3 — the monthly close as a durable, idempotent, restartable job
(`app.services.transport.close`), `BA_fleet_fuel.md` R31/R60. See that
module's docstring for what "the close" means in this codebase versus Fleet
Fuel's own consolidate/build_master/history pipeline (deliberately not
ported) and how R31's durability properties fall out of the pre-existing
`app.services.jobs` framework with no new mechanism.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.job import DEAD, Job
from app.models.transport.vat_claim import VatRefundClaimLine
from app.services import jobs
from app.services.transport import claim as claim_svc
from app.services.transport import close, fuel_ingest
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _make_claim(db_session, org, entity, **overrides):
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_txn(
    db_session, org, entity, *, invoice_ref="INV-0001", line_seq=1, period="2026-05"
):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
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
        invoice_ref=invoice_ref,
    )


@pytest.mark.asyncio
async def test_g1_3_validate_close_period_accepts_and_rejects():
    for good in ("2026-01", "2026-12", "2000-06"):
        close.validate_close_period(good)  # must not raise
    for bad in ("2026-13", "2026-00", "2026-1", "2026", "2026-Q1", "not-a-period", ""):
        with pytest.raises(AppError) as exc:
            close.validate_close_period(bad)
        assert exc.value.code == "invalid_close_period"
        assert exc.value.status == 422


@pytest.mark.asyncio
async def test_g1_3_run_close_rebuilds_claim_lines_for_in_scope_draft_claims(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)  # 2026-Q2 = 04,05,06
    await db_session.commit()
    await _make_txn(db_session, org, entity, period="2026-05", line_seq=1)
    await db_session.commit()

    result = await close.run_close(db_session, org.id, "2026-05")
    await db_session.commit()

    assert result["period"] == "2026-05"
    assert result["claims_processed"] == 1
    assert result["claims"][0]["claim_id"] == claim.id
    assert result["claims"][0]["line_count"] == 1

    lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_g1_3_run_close_ignores_claims_outside_the_period_scope(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_claim(db_session, org, entity, ref_period="2026-Q2")  # 04,05,06
    await db_session.commit()

    result = await close.run_close(db_session, org.id, "2026-01")  # outside Q2
    assert result["claims_processed"] == 0
    assert result["claims"] == []


@pytest.mark.asyncio
async def test_g1_3_run_close_ignores_non_draft_claims(db_session):
    """G1.4: a claim that has left `draft` is invisible to the close's own
    query filter — the close never even attempts to touch it."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    claim.status = "submitted"
    await db_session.commit()
    await _make_txn(db_session, org, entity, period="2026-05")
    await db_session.commit()

    result = await close.run_close(db_session, org.id, "2026-05")
    assert result["claims_processed"] == 0


@pytest.mark.asyncio
async def test_g1_3_run_close_is_idempotent_rerunning_does_not_duplicate_lines(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity, period="2026-05")
    await db_session.commit()

    await close.run_close(db_session, org.id, "2026-05")
    await db_session.commit()
    await close.run_close(db_session, org.id, "2026-05")
    await db_session.commit()

    lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_g1_3_module_off_run_close_is_inert(db_session):
    org = await make_org(db_session)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await close.run_close(db_session, org.id, "2026-05")
    assert exc.value.code == "module_not_enabled"
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_g1_3_audit_event_fires_once_per_close_run(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_claim(db_session, org, entity)
    await db_session.commit()

    await close.run_close(db_session, org.id, "2026-05")
    await db_session.commit()
    await close.run_close(db_session, org.id, "2026-05")
    await db_session.commit()

    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id, AuditEvent.action == "transport.close_run"
            )
        )
    ).all()
    assert len(events) == 2


@pytest.mark.asyncio
async def test_g1_3_enqueue_close_is_idempotent_by_org_and_period(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_claim(db_session, org, entity)
    await db_session.commit()

    job_id_1 = await close.enqueue_close(db_session, org.id, "2026-05")
    job_id_2 = await close.enqueue_close(db_session, org.id, "2026-05")
    assert job_id_1 == job_id_2

    rows = (
        await db_session.scalars(
            select(Job).where(
                Job.org_id == org.id, Job.kind == close.CLOSE_KIND, Job.idempotency_key.isnot(None)
            )
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_g1_3_enqueue_close_refuses_a_malformed_period(db_session):
    org = await make_org(db_session)
    with pytest.raises(AppError) as exc:
        await close.enqueue_close(db_session, org.id, "2026-Q2")
    assert exc.value.code == "invalid_close_period"


@pytest.mark.asyncio
async def test_g1_3_run_once_through_the_job_worker_processes_the_enqueued_close(db_session):
    """The close never runs inline — proves it end-to-end through the SAME
    worker entry point every other job kind uses (R60)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity, period="2026-05")
    await db_session.commit()

    await close.enqueue_close(db_session, org.id, "2026-05")
    job = await jobs.run_once(db_session, "test-worker")

    assert job is not None
    assert job.kind == close.CLOSE_KIND
    assert job.status == "succeeded"

    lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_g1_3_halts_on_first_failure_nothing_partially_applied(db_session, monkeypatch):
    """R31 verbatim: a failure partway through the claims loop rolls back
    EVERY claim-line rebuild from that run, not just the one that raised —
    proven by forcing the second of two claims to blow up and asserting the
    FIRST claim's lines were never persisted either."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim_a = await _make_claim(db_session, org, entity, refund_country="LV")
    claim_b = await _make_claim(db_session, org, entity, refund_country="EE")
    claim_a_id, claim_b_id = claim_a.id, claim_b.id
    await db_session.commit()
    await _make_txn(db_session, org, entity, period="2026-05", line_seq=1)
    await db_session.commit()

    import app.services.transport.close as close_mod

    real_build = close_mod.build_claim_lines
    call_count = {"n": 0}

    async def _boom(db, org_id, claim_id):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure on the second claim")
        return await real_build(db, org_id, claim_id)

    monkeypatch.setattr(close_mod, "build_claim_lines", _boom)

    await close.enqueue_close(db_session, org.id, "2026-05")
    job = await jobs.run_once(db_session, "test-worker")

    assert job is not None
    assert job.status in ("queued", DEAD)  # retried or dead-lettered, never succeeded
    assert "simulated failure" in (job.last_error or "")

    # Nothing partially applied: EITHER claim's lines table is empty — the
    # whole session rolled back, not just claim_b's failed insert.
    all_lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(
                VatRefundClaimLine.claim_id.in_([claim_a_id, claim_b_id])
            )
        )
    ).all()
    assert all_lines == []
