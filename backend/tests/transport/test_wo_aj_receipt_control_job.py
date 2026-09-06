"""WO-AJ — the receipt-control RUN as a job kind (`transport.receipt_control`).

`run_receipt_control` was never routed by design (R60: a whole-period
expectation walk never runs inline in a request), so between two monthly closes
an operator had no way to refresh the chase list. The job door reuses the
existing rails — `jobs.enqueue` + `USER_ENQUEUEABLE` — and the engine's own
contract (§3.J item 4: overrides survive re-runs) holds through it.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.models import job as jobmodel
from app.services import job_handlers, jobs
from app.services.transport import receipt_control
from tests.transport.conftest import enable_transport, make_entity, make_org
from tests.transport.test_g3_5_receipt_control import PERIOD, _make_txn


@pytest.mark.asyncio
async def test_wo_aj_the_queue_runs_the_engine_and_reports_its_summary(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    # Activity with no registered invoice — the one finding worth chasing.
    await _make_txn(
        db_session, org, entity, supplier="BP", invoice_ref=None, txn_date=date(2026, 5, 10)
    )
    await db_session.commit()

    job = await jobs.enqueue(
        db_session, job_handlers.RECEIPT_CONTROL_RUN, {"period": PERIOD}, org_id=org.id
    )
    done = await jobs.run_once(db_session, "control-worker")
    assert done is not None and done.id == job.id
    assert done.status == jobmodel.SUCCEEDED, done.last_error
    summary = json.loads(done.result_json or "{}")
    assert summary["period"] == PERIOD
    assert summary["missing"] == 1

    rows = await receipt_control.list_controls(db_session, org.id, PERIOD)
    assert [(r.supplier, r.status) for r in rows] == [("BP", "missing")]

    # §3.J item 4 through the job door: a mute set between runs survives the re-run.
    await receipt_control.set_control_override(db_session, org.id, rows[0].id, waived=True)
    await db_session.commit()
    await jobs.enqueue(
        db_session, job_handlers.RECEIPT_CONTROL_RUN, {"period": PERIOD}, org_id=org.id
    )
    again = await jobs.run_once(db_session, "control-worker")
    assert again is not None and again.status == jobmodel.SUCCEEDED, again.last_error
    rows = await receipt_control.list_controls(db_session, org.id, PERIOD)
    assert rows[0].waived is True


@pytest.mark.asyncio
async def test_wo_aj_a_run_without_a_period_fails_with_the_named_reason(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await db_session.commit()
    await jobs.enqueue(db_session, job_handlers.RECEIPT_CONTROL_RUN, None, org_id=org.id)
    done = await jobs.run_once(db_session, "control-worker")
    assert done is not None and done.status != jobmodel.SUCCEEDED
    assert "period" in (done.last_error or "")


@pytest.mark.asyncio
async def test_wo_aj_the_module_gate_holds_inside_the_worker(db_session):
    """An org without the transport module gets no grid — fail closed, as every
    transport service entry point does, whoever the caller is."""
    org = await make_org(db_session)
    await db_session.commit()
    await jobs.enqueue(
        db_session, job_handlers.RECEIPT_CONTROL_RUN, {"period": PERIOD}, org_id=org.id
    )
    done = await jobs.run_once(db_session, "control-worker")
    assert done is not None and done.status != jobmodel.SUCCEEDED
    assert "module" in (done.last_error or "").lower()


@pytest.mark.asyncio
async def test_wo_aj_the_kind_is_enqueueable_over_http(auth_client):
    r = await auth_client.post(
        "/api/v1/jobs",
        json={"kind": "transport.receipt_control", "payload": {"period": PERIOD}},
    )
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "transport.receipt_control"
