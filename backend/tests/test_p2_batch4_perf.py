"""P2 batch 4 (audit 2026-09-05) — the performance group.

- PERF-006: the project P&L summary reads the whole tenant in a fixed number
  of statements and agrees, figure for figure, with the per-project detail.
- PERF-001: reconciliation candidates are narrowed by the amount window IN
  SQL; the window itself (±0.02) is unchanged.
- ARCH-013 / PERF-011: the daily scheduler reads what is already queued in
  one statement and commits once; a repeat sweep costs a constant number of
  statements whatever the tenant count.
- PERF-009: an integrity sweep larger than `integrity_sync_limit` is queued as
  the matching background job (202) instead of running inside the request;
  the queued job runs and reports.
- PERF-017: `gc_gen2_threshold` is a measurement knob — applied when set,
  the interpreter default untouched when not.

Industry-neutral fixtures (site crews, yards, generic suppliers).
"""

from __future__ import annotations

import gc
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.core import gc_tuning
from app.core.config import settings
from app.models.expense import ExpenseItem, ExpenseReport
from app.models.job import Job
from app.models.organization import Organization
from app.models.project_offer import ProjectOffer
from app.models.user import User
from app.services import job_handlers, jobs, project_profit, scheduler
from tests.test_integrity import _upload_logo
from tests.test_project_allocation import _invoice as _supplier_invoice
from tests.test_project_allocation import _transition
from tests.test_project_profitability import _issue, _issuer_ready, _pnl, _project
from tests.test_reconciliation import _enable, _upload


class _Counter:
    """Every statement the engine executes while the block runs."""

    def __init__(self, db_session):
        engine = db_session.bind
        self.engine = getattr(engine, "sync_engine", engine)
        self.statements: list[str] = []

    def _count(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._count)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._count)


async def _org(db) -> str:
    return await db.scalar(select(Organization.id).where(Organization.name == "Acme"))


_PNL_KEYS = (
    "revenue",
    "credited",
    "costs",
    "invoice_costs",
    "expense_costs",
    "manual_costs",
    "profit",
    "margin_pct",
    "estimated_revenue",
    "basis",
    "adjustments",
    "status",
)


async def _book_everything(auth_client, db_session, org_id: str, suffix: str) -> list[str]:
    """Three projects exercising every input of the P&L: issued revenue and a
    credit note, a whole-invoice allocation, a percentage split with a tagged
    line, an approved expense, manual cost lines (one negative), two accepted
    offer versions, a closed project with a post-close adjustment, and a draft
    issued invoice that must not count."""
    p1 = await _project(auth_client, f"YARD-{suffix}-1", "Yard extension")
    p2 = await _project(auth_client, f"YARD-{suffix}-2", "Roof repair")
    p3 = await _project(auth_client, f"YARD-{suffix}-3", "Fence line")

    issued = await _issue(auth_client, p1, amount="1000.00")
    cn = await auth_client.post(f"/api/v1/issued/{issued['id']}/credit-note", json={})
    assert cn.status_code == 201, cn.text
    await _issue(auth_client, p2, amount="500.00")
    await _issue(auth_client, p3, amount="80.00", draft=True)

    inv_a, _ = await _supplier_invoice(
        db_session, org_id, subtotal="200.00", number=f"SUP-{suffix}-A"
    )
    r = await auth_client.put(f"/api/v1/invoices/{inv_a}/allocation", json={"project_id": p1})
    assert r.status_code == 200, r.text

    inv_b, lines = await _supplier_invoice(
        db_session, org_id, subtotal="100.01", number=f"SUP-{suffix}-B", lines=("10.00", "90.01")
    )
    r = await auth_client.put(
        f"/api/v1/invoices/{inv_b}/allocation",
        json={
            "project_id": p2,
            "splits": [{"project_id": p1, "percent": "60"}, {"project_id": p2, "percent": "40"}],
            "lines": {lines[0]: p3},
        },
    )
    assert r.status_code == 200, r.text

    owner_id = await db_session.scalar(select(User.id).where(User.email == "owner@acme.io"))
    report = ExpenseReport(
        org_id=org_id,
        employee_id=owner_id,
        employee_name="Owner",
        title=f"Crew expenses {suffix}",
        status="approved",
    )
    db_session.add(report)
    await db_session.flush()
    db_session.add(
        ExpenseItem(
            report_id=report.id,
            org_id=org_id,
            description="Fuel for the crew van",
            spend_date=date(2026, 8, 2),
            amount=Decimal("61.00"),
            vat_amount=Decimal("11.00"),
            project_id=p2,
        )
    )
    for version, total in ((1, "900.00"), (2, "950.00")):
        db_session.add(
            ProjectOffer(
                org_id=org_id,
                project_id=p2,
                number=f"OFF-{suffix}",
                version=version,
                status="accepted",
                total=Decimal(total),
            )
        )
    await db_session.commit()

    for pid, label, amount in ((p1, "Crew wages", "300.00"), (p3, "Returned skip", "-20.00")):
        r = await auth_client.post(
            f"/api/v1/masters/projects/{pid}/cost-entries",
            json={"label": label, "category": "wages", "amount": amount},
        )
        assert r.status_code == 201, r.text

    await _transition(auth_client, p3, "closed")
    # The supplier invoice for the last fence posts arrives after the close.
    inv_c, _ = await _supplier_invoice(
        db_session, org_id, subtotal="15.50", number=f"SUP-{suffix}-C"
    )
    r = await auth_client.put(f"/api/v1/invoices/{inv_c}/allocation", json={"project_id": p3})
    assert r.status_code == 200, r.text
    return [p1, p2, p3]


@pytest.mark.asyncio
async def test_perf006_summary_equals_the_detail_and_its_sql_does_not_grow(auth_client, db_session):
    org_id = await _org(db_session)
    await _issuer_ready(auth_client)
    first = await _book_everything(auth_client, db_session, org_id, "A")

    with _Counter(db_session) as c:
        r = await auth_client.get("/api/v1/masters/projects-pnl-summary")
        assert r.status_code == 200, r.text
        small = len(c.statements)
    rows = {row["project_id"]: row for row in r.json()}
    assert set(rows) == set(first)

    # Figure for figure, the list says what the detail says — including the
    # frozen project's adjustments and the estimate from the later version.
    for pid in first:
        detail = await _pnl(auth_client, pid)
        for key in _PNL_KEYS:
            assert rows[pid][key] == detail[key], (key, rows[pid], detail)
    p1, p2, p3 = first
    assert rows[p1]["revenue"] == "0.00" and rows[p1]["credited"] == "1000.00"
    assert rows[p1]["invoice_costs"] == "254.01"  # 200.00 + 60% of 90.01 (=54.01)
    assert rows[p2]["invoice_costs"] == "36.00"  # 40% of 90.01, residue on the 60 share
    assert rows[p2]["expense_costs"] == "50.00"
    assert rows[p2]["estimated_revenue"] == "950.00"
    assert rows[p3]["basis"] == "net_eur_frozen"
    assert rows[p3]["invoice_costs"] == "10.00"  # the tagged line
    assert rows[p3]["adjustments"] == {
        "invoice_costs": "15.50",
        "costs": "15.50",
        "profit": "-15.50",
    }

    # Three times the projects and the allocations: the same statements.
    await _book_everything(auth_client, db_session, org_id, "B")
    await _book_everything(auth_client, db_session, org_id, "C")
    with _Counter(db_session) as c:
        r = await auth_client.get("/api/v1/masters/projects-pnl-summary")
        assert r.status_code == 200 and len(r.json()) == 9
        large = len(c.statements)
    assert large == small, f"{small} statements for 3 projects, {large} for 9"


@pytest.mark.asyncio
async def test_perf006_bulk_figures_match_the_single_project_path(auth_client, db_session):
    """The service-level contract behind the route test: for every project the
    bulk figures ARE the single-project figures, and a project with nothing
    booked simply has no entry (the caller reads zeros)."""
    org_id = await _org(db_session)
    await _issuer_ready(auth_client)
    projects = await _book_everything(auth_client, db_session, org_id, "S")
    empty = await _project(auth_client, "YARD-S-EMPTY", "Nothing booked")

    bulk = await project_profit._live_figures_bulk(db_session, org_id)
    for pid in projects:
        assert bulk[pid] == await project_profit._live_figures(db_session, org_id, pid)
    assert empty not in bulk
    assert await project_profit._live_figures(db_session, org_id, empty) == dict.fromkeys(
        project_profit._FROZEN_KEYS, "0.00"
    )


@pytest.mark.asyncio
async def test_perf001_candidates_are_narrowed_by_amount_in_sql(auth_client, db_session):
    """A receipt 0.02 off the bank line is still a candidate, 0.03 off is not
    (the window is the one the code always had) — and the receipts query
    carries that window as a BETWEEN, so the tenant's cash tables are no
    longer loaded whole and filtered in Python."""
    await _enable(auth_client)
    near = (
        await auth_client.post(
            "/api/v1/receipts", json={"amount": "1210.02", "received_on": "2026-02-05"}
        )
    ).json()
    far = (
        await auth_client.post(
            "/api/v1/receipts", json={"amount": "1210.03", "received_on": "2026-02-05"}
        )
    ).json()
    await _upload(auth_client)
    sid = (await auth_client.get("/api/v1/reconciliation/statements")).json()[0]["id"]
    lines = (await auth_client.get(f"/api/v1/reconciliation/statements/{sid}/lines")).json()
    credit = next(ln for ln in lines if ln["direction"] == "credit")

    with _Counter(db_session) as c:
        r = await auth_client.get(f"/api/v1/reconciliation/lines/{credit['id']}/candidates")
    assert r.status_code == 200, r.text
    ids = {cand["id"] for cand in r.json()}
    assert near["id"] in ids and far["id"] not in ids
    receipt_reads = [s for s in c.statements if "FROM receipts" in s]
    assert receipt_reads and all("BETWEEN" in s for s in receipt_reads), receipt_reads


async def _second_org(client, name: str) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": name,
            "name": "Owner",
            "email": f"owner@{name}.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_arch013_daily_sweep_prefetches_once_and_repeats_in_constant_statements(
    auth_client, client, db_session
):
    today = date(2026, 7, 20)
    created = await scheduler.enqueue_daily(db_session, today=today)
    assert created == len(scheduler.DAILY_KINDS) + 1  # every daily kind + the FX carrier

    with _Counter(db_session) as one_org:
        assert await scheduler.enqueue_daily(db_session, today=today) == 0

    carrier_before = min(await db_session.scalars(select(Organization.id)))
    await _second_org(client, "sitecrew")
    # The FX carrier is the LOWEST org id (unchanged rule): when the new
    # tenant's id sorts lower it carries today's refresh — the handler
    # dedupes on the shared per-day key, so nothing is fetched twice.
    carrier_moved = min(await db_session.scalars(select(Organization.id))) != carrier_before
    assert await scheduler.enqueue_daily(db_session, today=today) == len(scheduler.DAILY_KINDS) + (
        1 if carrier_moved else 0
    )
    with _Counter(db_session) as two_orgs:
        assert await scheduler.enqueue_daily(db_session, today=today) == 0

    # A repeat sweep is one prefetch of today's keys plus the fixed "who is
    # due" reads — never one existence probe per (tenant, kind).
    assert len(one_org.statements) == len(two_orgs.statements), (
        one_org.statements,
        two_orgs.statements,
    )
    assert len(two_orgs.statements) < 2 * len(scheduler.DAILY_KINDS)
    assert not [s for s in two_orgs.statements if s.startswith("INSERT")]

    # Tomorrow is a new day for both tenants.
    assert await scheduler.enqueue_daily(db_session, today=date(2026, 7, 21)) == (
        2 * len(scheduler.DAILY_KINDS) + 1
    )
    keys = set(await db_session.scalars(select(Job.idempotency_key)))
    assert f"{job_handlers.FX_REFRESH}:2026-07-20" in keys


@pytest.mark.asyncio
async def test_perf009_a_large_sweep_is_queued_and_the_job_reports(
    auth_client, db_session, monkeypatch
):
    await _upload_logo(auth_client)

    # One logo fits any limit: synchronous, 200, the report as before.
    r = await auth_client.post("/api/v1/integrity/documents/verify")
    assert r.status_code == 200 and r.json()["checked"] == 1, r.text

    # Above the limit the route queues the job and says so.
    monkeypatch.setattr(settings, "integrity_sync_limit", 0)
    with _Counter(db_session) as c:
        r = await auth_client.post("/api/v1/integrity/documents/verify")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["queued"] is True and body["created"] is True
    assert body["job_kind"] == job_handlers.INTEGRITY_VERIFY
    assert body["references"] == 1 and body["sync_limit"] == 0
    # Counted, not verified: no document row was read inside the request.
    assert not [s for s in c.statements if "FROM documents" in s]

    # A second click the same day hands back the queued job, not a second one.
    again = await auth_client.post("/api/v1/integrity/documents/verify")
    assert again.status_code == 202 and again.json()["job_id"] == body["job_id"]
    assert again.json()["created"] is False

    # The version chain has one slot (the logo's): queued. The ledger has no
    # row at all: nothing to defer, verified in the request as before.
    r = await auth_client.post("/api/v1/integrity/versions/verify")
    assert r.status_code == 202 and r.json()["job_kind"] == job_handlers.INTEGRITY_VERSIONS
    r = await auth_client.post("/api/v1/integrity/ledger/verify")
    assert r.status_code == 200 and r.json()["checked"] == 0, r.text
    kinds = set(await db_session.scalars(select(Job.kind)))
    assert kinds == {job_handlers.INTEGRITY_VERIFY, job_handlers.INTEGRITY_VERSIONS}

    job = await jobs.run_once(db_session, "w")
    assert job is not None and job.status == "succeeded", job
    result = json.loads(job.result_json)
    assert result["healthy"] is True and result["checked"] == 1


def test_perf017_gen2_threshold_is_applied_at_startup_and_none_leaves_the_interpreter_alone(
    monkeypatch,
):
    """The default is 100 (measured — `app/core/gc_tuning.py`), applied by the
    same startup call that freezes the heap; None keeps whatever the
    interpreter has, so the setting can be switched off without a release."""
    before = gc.get_threshold()
    try:
        assert settings.model_fields["gc_gen2_threshold"].default == 100
        monkeypatch.setattr(settings, "gc_gen2_threshold", None)
        assert gc_tuning.apply_gen2_threshold() == before
        monkeypatch.setattr(settings, "gc_gen2_threshold", 100)
        assert gc_tuning.apply_gen2_threshold() == (before[0], before[1], 100)
        assert gc.get_threshold()[2] == 100
        gc.set_threshold(*before)
        monkeypatch.setattr(settings, "gc_gen2_threshold", 100)
        # The startup call applies it too — without parking this test
        # process's heap for good (the PERF-016 test measures a real freeze).
        monkeypatch.setattr(gc, "freeze", lambda: None)
        gc_tuning.freeze_startup_heap()
        assert gc.get_threshold()[2] == 100, "the freeze call applies the threshold too"
    finally:
        gc.set_threshold(*before)
