"""The perf harness's dataset must exercise the reads it measures (PERF-004's
lesson: three scenarios once measured empty sets). Reference R5 (P-4): the AP
inbox on the dashboard (`approval_policy.waiting_for`, PERF-DUCK-001) reduced
NOTHING at every scale because the seed created no approval steps — so the
pushdown's own `--shape` datapoint did not exist. This proves the seed now
gives that read rows to walk, on the suite's SQLite (the seed is engine-
agnostic; only the measurement refuses SQLite)."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.tenant import reset_current_org, set_current_org
from app.models.approval import STEP_PENDING, ApprovalStep
from app.models.invoice import Invoice, WorkflowState
from app.models.issuer import IssuerProfile
from app.services import approval_policy

HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "perf_harness.py"


def _harness():
    spec = importlib.util.spec_from_file_location("_perf_harness_seed", HARNESS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_perf_harness_seed"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_the_seed_gives_the_ap_inbox_rows_to_walk(auth_client, db_session):
    me = (await auth_client.get("/api/v1/auth/me")).json()
    org_id, user_id = me["organization"]["id"], me["user"]["id"]
    token = set_current_org(org_id)
    try:
        entity = IssuerProfile(org_id=org_id, name="Perf Entity", legal_name="Perf Entity OU")
        db_session.add(entity)
        await db_session.commit()
        await db_session.refresh(entity)
        await _harness()._seed_scale(db_session, org_id, entity.id, 60)

        submitted = await db_session.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.org_id == org_id,
                Invoice.workflow_state.in_(
                    (WorkflowState.submitted, WorkflowState.partially_approved)
                ),
            )
        )
        assert submitted and submitted > 0, "the workflow spread must include submitted invoices"
        pending_steps = await db_session.scalar(
            select(func.count())
            .select_from(ApprovalStep)
            .where(ApprovalStep.org_id == org_id, ApprovalStep.status == STEP_PENDING)
        )
        assert pending_steps == submitted, "one generic pending step per submitted invoice"

        # And the read the dashboard runs sees them (approve-any, nobody's own
        # submission excluded by segregation of duties, LIMIT honoured).
        rows = await approval_policy.waiting_for(
            db_session, org_id, user_id=user_id, can_approve_any=True, limit=10
        )
        assert len(rows) == min(10, submitted)
        rows_all = await approval_policy.waiting_for(
            db_session, org_id, user_id=user_id, can_approve_any=True, limit=1000
        )
        assert len(rows_all) == submitted
        assert {r.invoice_number[:5] for r in rows_all} == {"PERF-"}
    finally:
        reset_current_org(token)


# --- Postgres: the seed is analysed (PERF-020) ------------------------------


@pytest.mark.asyncio
async def test_the_harness_measures_the_dashboard_with_its_receivables_card(
    auth_client, db_session
):
    """PERF-018: `issuing` is default-OFF, so until this the harness — which
    enabled only `transport` — measured a dashboard whose receivables card was
    SKIPPED. That is the one shape in which the duplicate receivables read
    could not happen, which is why no run since PERF-003 saw it. The seed has
    always filled `issued_invoices`; the module was the only thing in the way.

    Read off the harness's own source rather than by running it (the harness
    refuses SQLite by design), so this stays a statement about what the CI job
    executes."""
    source = HARNESS.read_text(encoding="utf-8")
    for key in ("transport", "issuing"):
        assert f'set_enabled(db, org_id, "{key}", True)' in source, (
            f"the harness stopped enabling `{key}` — it is measuring a shape "
            "the product does not run"
        )


RLS_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not RLS_URL, reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the Postgres proof"
)


@pg_only
@pytest.mark.asyncio
async def test_the_seed_is_analysed_on_postgres_so_the_gate_measures_steady_state_plans():
    """PERF-020: a bulk seed into never-analysed tables gave the planner a
    one-row estimate and the AP-inbox anti join ran as a nested loop over
    every pending step — 8.08× growth at 2,000 → 8,000 that `ANALYZE` alone
    took to 1.0×. The harness now analyses what it seeds; this proves the
    seeded tables carry no unanalysed modifications afterwards."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.tenant import reset_current_org as _reset
    from app.core.tenant import set_current_org as _set

    harness = _harness()
    engine = create_async_engine(RLS_URL)
    org_id = str(uuid.uuid4())
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO organizations (id, name, ai_validation_enabled, "
                    "human_validation_enabled, plan, status, created_at, updated_at) "
                    "VALUES (:id, 'Seed Org', false, false, 'trial', 'active', now(), now())"
                ),
                {"id": org_id},
            )
        token = _set(org_id)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                entity = IssuerProfile(org_id=org_id, name="Seed Entity", legal_name="Seed OU")
                db.add(entity)
                await db.commit()
                await db.refresh(entity)
                t0 = await db.scalar(text("SELECT now()"))
                await harness._seed_scale(db, org_id, entity.id, 40)
                await harness._analyze_after_seed(db)
                # `last_analyze` is stamped by a manual ANALYZE; the stats view
                # flushes shortly after commit, so poll briefly. (A modification
                # counter would race with any other writer on a shared DB.)
                for _ in range(30):
                    stale = (
                        await db.execute(
                            text(
                                "SELECT relname FROM pg_stat_user_tables "
                                "WHERE relname = ANY(:t) "
                                "AND (last_analyze IS NULL OR last_analyze < :t0)"
                            ),
                            {"t": list(harness._SEEDED_TABLES), "t0": t0},
                        )
                    ).all()
                    if not stale:
                        break
                    await asyncio.sleep(0.1)
                assert stale == [], f"seeded tables not analysed after the seed: {stale}"
        finally:
            _reset(token)
    finally:
        async with engine.begin() as conn:
            # FORCE RLS: the fuel-transactions policy shows nothing to a
            # connection without a tenant setting, so the delete would match
            # zero rows and the issuer profile's RESTRICT would then refuse.
            await conn.execute(
                text("SELECT set_config('app.current_org', :o, true)"), {"o": org_id}
            )
            for table in (
                "approval_steps",
                "line_items",
                "payments",
                "issued_invoices",
                "fuel_transactions",
                "invoices",
                "vendors",
                "issuer_profiles",
            ):
                col = "org_id"
                if table == "line_items":
                    await conn.execute(
                        text(
                            "DELETE FROM line_items WHERE invoice_id IN "
                            "(SELECT id FROM invoices WHERE org_id = :o)"
                        ),
                        {"o": org_id},
                    )
                    continue
                await conn.execute(text(f"DELETE FROM {table} WHERE {col} = :o"), {"o": org_id})  # noqa: S608
            await conn.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
        await engine.dispose()
