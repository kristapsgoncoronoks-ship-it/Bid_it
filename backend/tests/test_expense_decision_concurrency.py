"""Postgres-only proof that two genuinely concurrent expense-approval decisions
on the SAME pending step settle exactly once (WO-30/R4, invariant §4.13: "no
over-payment/over-crediting/over-decision, enforced under a row lock").

`decide()` (`app/api/routes/expenses.py`) now loads the report `SELECT … FOR
UPDATE` (via `_load(..., lock=True)`) and version-checks it before mutating the
current pending `ExpenseApprovalStep` + the report's status/version, so two
overlapping decisions on the same open step serialize on the report row: the
loser re-reads the step as no-longer-pending (or a version mismatch) after the
winner commits, and its transaction changes NOTHING — no second `STEP_APPROVED`
write, no second `expense.approved` webhook fan-out.

Fires through the REAL HTTP API (not a directly-callable service function — the
locking/version logic lives in the route itself, same reasoning as
tests/test_credit_note_lock_concurrency.py), because there is no standalone
`decide_report(...)` service function to call the way
tests/test_payment_run_pay_concurrency.py calls `payment_run.mark_paid`
directly. Postgres-only (SQLite's test harness is one shared connection — no
real concurrency), mirroring tests/test_payment_run_pay_concurrency.py and
tests/test_credit_note_lock_concurrency.py.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from app.models.expense_approval import STEP_APPROVED, ExpenseApprovalStep
from app.models.webhook import WebhookDelivery
from app.services import fx

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the concurrency test"
)

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pg_only
@pytest.mark.asyncio
async def test_two_concurrent_decisions_settle_once():
    # pool_size=2: the two decision requests below run genuinely concurrently
    # (asyncio.gather + a barrier), each holding its own connection for the
    # duration of its transaction — mirrors
    # tests/test_credit_note_lock_concurrency.py's pool_size=2 reasoning.
    engine = create_async_engine(PG_URL, pool_size=2, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _get_test_session():
        async with sm() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    transport = ASGITransport(app=app)
    try:
        # ECB rates aren't seeded by a lifespan under ASGITransport (mirrors
        # tests/conftest.py::_db and test_credit_note_lock_concurrency.py).
        async with sm() as session:
            await fx.ensure_seed_rates(session, date(2026, 7, 18))
            await fx.ensure_european_coverage(session, date(2026, 7, 18))

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            suffix = uuid.uuid4().hex[:8]
            reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": f"Race Expenses Co {suffix}",
                    "name": "Owner",
                    "email": f"owner-{suffix}@race.io",
                    "password": "supersecret",
                },
            )
            assert reg.status_code == 201, reg.text
            owner_tok = reg.json()["token"]["access_token"]
            org_id = reg.json()["user"]["org_id"]
            owner_headers = _h(owner_tok)

            assert (
                await client.put(
                    "/api/v1/modules/expenses", json={"enabled": True}, headers=owner_headers
                )
            ).status_code == 200

            # A second approver ("Ann") — the owner is an approver by default, so
            # this gives the generic open step (no policy configured → approver_id
            # IS NULL) TWO legitimate approvers who can both legally decide it —
            # the reachable-by-default race, not a contrived one.
            inv_ann = await client.post(
                "/api/v1/team/invites",
                json={"email": f"ann-{suffix}@race.io", "role": "user"},
                headers=owner_headers,
            )
            assert inv_ann.status_code == 201, inv_ann.text
            acc_ann = await client.post(
                "/api/v1/auth/accept-invite",
                json={
                    "token": inv_ann.json()["token"],
                    "name": "Ann",
                    "password": "supersecret",
                },
            )
            assert acc_ann.status_code == 201, acc_ann.text
            ann_tok = acc_ann.json()["token"]["access_token"]
            ann_id = acc_ann.json()["user"]["id"]
            ann_headers = _h(ann_tok)
            appointed = await client.patch(
                f"/api/v1/team/members/{ann_id}",
                json={"is_expense_approver": True},
                headers=owner_headers,
            )
            assert appointed.status_code == 200, appointed.text

            # The employee whose report is decided — neither approver (SoD: an
            # approver cannot decide their own report).
            inv_emp = await client.post(
                "/api/v1/team/invites",
                json={"email": f"emp-{suffix}@race.io", "role": "user"},
                headers=owner_headers,
            )
            assert inv_emp.status_code == 201, inv_emp.text
            acc_emp = await client.post(
                "/api/v1/auth/accept-invite",
                json={
                    "token": inv_emp.json()["token"],
                    "name": "Employee",
                    "password": "supersecret",
                },
            )
            assert acc_emp.status_code == 201, acc_emp.text
            emp_headers = _h(acc_emp.json()["token"]["access_token"])

            created = await client.post(
                "/api/v1/expenses",
                headers=emp_headers,
                json={
                    "title": "Race trip",
                    "items": [
                        {
                            "spend_date": "2026-05-01",
                            "category": "meals",
                            "description": "Dinner",
                            "amount": "42.00",
                            "vat_amount": "0",
                        }
                    ],
                },
            )
            assert created.status_code == 201, created.text
            rid = created.json()["id"]
            rep = (await client.get(f"/api/v1/expenses/{rid}", headers=emp_headers)).json()
            for it in rep["items"]:
                await client.patch(
                    f"/api/v1/expenses/{rid}/items/{it['id']}",
                    json={"comment": "Client visit"},
                    headers=emp_headers,
                )
                await client.post(
                    f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
                    files={"file": ("r.png", _PNG, "image/png")},
                    headers=emp_headers,
                )
            submitted = await client.post(f"/api/v1/expenses/{rid}/submit", headers=emp_headers)
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["version"] == 1
            # No approval policy configured → one generic OPEN step (approver_id
            # NULL) — either approver may legally decide it.
            assert submitted.json()["approval_steps"][0]["approver_id"] is None

            # A webhook endpoint subscribed to the event this decision fires —
            # proves the fix doesn't just avoid a double 200, it avoids a double
            # fan-out to a real integration.
            hook = await client.post(
                "/api/v1/webhooks",
                json={"url": "https://example.test/hook", "events": "expense.approved"},
                headers=owner_headers,
            )
            assert hook.status_code == 201, hook.text

            barrier = asyncio.Barrier(2)

            async def decide(headers: dict[str, str]) -> object:
                await barrier.wait()  # maximize the overlap
                return await client.post(
                    f"/api/v1/expenses/{rid}/decision",
                    json={"action": "approve", "version": 1},
                    headers=headers,
                )

            r_owner, r_ann = await asyncio.gather(decide(owner_headers), decide(ann_headers))

            # Exactly one winner (200) and one loser (409, not silently applied).
            codes = sorted([r_owner.status_code, r_ann.status_code])
            assert codes == [200, 409], (
                r_owner.status_code,
                r_owner.text,
                r_ann.status_code,
                r_ann.text,
            )

            got = await client.get(f"/api/v1/expenses/{rid}", headers=owner_headers)
            assert got.status_code == 200, got.text
            assert got.json()["status"] == "approved"
            assert got.json()["version"] == 2  # bumped exactly once, not twice

            async with sm() as session:
                # This raw session sets no `app.current_org` GUC, so — same as
                # the app's own defence-in-depth query filters — scope explicitly
                # by org_id rather than relying on RLS's unscoped-session default
                # (which would otherwise see every org's rows on a DB shared
                # across test runs, not just this test's).
                approved_steps = await session.scalar(
                    select(func.count())
                    .select_from(ExpenseApprovalStep)
                    .where(
                        ExpenseApprovalStep.org_id == org_id,
                        ExpenseApprovalStep.report_id == rid,
                        ExpenseApprovalStep.status == STEP_APPROVED,
                    )
                )
                assert approved_steps == 1  # the step itself is not double-written

                deliveries = await session.scalar(
                    select(func.count())
                    .select_from(WebhookDelivery)
                    .where(
                        WebhookDelivery.org_id == org_id,
                        WebhookDelivery.event_type == "expense.approved",
                    )
                )
                assert deliveries == 1  # no double fan-out to the integration
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
