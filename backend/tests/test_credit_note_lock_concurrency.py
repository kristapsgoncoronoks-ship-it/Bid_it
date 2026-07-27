"""Postgres-only proof that two truly-concurrent partial credit notes against the
SAME invoice sum correctly in `credited_total` (WO-26/R2, invariant §4.9/§4.10:
"no overpayment / no over-crediting, enforced under a row lock").

`create_credit_note` reads the corrected invoice, computes `already_credited`
from it, and writes `credited_total = already + this_note` — a read-modify-write
over a cumulative figure `record_payment` (WO-9) already locks for. Without a
lock, two overlapping partial credit-note requests both read the same stale
`already` and one write is lost, silently UNDERSTATING `credited_total` — which
widens the exact over-payment/over-credit exposure `record_payment` trusts that
number to cap.

This fires the requests through the REAL HTTP API (not the service function
directly), because the bug and its fix both live in the route's `_load(...,
lock=True)` call, not in a directly-testable service function. Postgres-only
(SQLite's test harness is one shared connection — no real concurrency), mirroring
tests/test_payment_run_pay_concurrency.py and tests/test_numbering_concurrency.py.

Uses a normal small pooled engine (`pool_size=1`), NOT `NullPool`. It used to
use `NullPool` (a brand-new physical connection per session, never reused) to
sidestep an unrelated, pre-existing defect this was otherwise the first test
to trip: Postgres custom GUCs (`app.current_org`) have no true "unset" state
once `set_config(..., true)` has run on a backend connection at least once —
`current_setting(name, true)` then returns `''`, never SQL NULL, for the rest
of that connection's life. That defect is now fixed (WO-27, migration
6fec8c88ba7c — every RLS policy's "unscoped" check now also matches `''`, not
just `IS NULL`; see `tests/test_rls_connection_reuse.py` and
`docs/architecture/adr/0028-rls-unscoped-guc-sticky-empty-string.md`), so this
test now runs on an ordinary reused pool like production does — `NullPool`
was a deliberate workaround for that bug, not the intended long-term pattern.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from app.services import fx

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the concurrency test"
)

ISSUER = {
    "legal_name": "Race Credit Notes BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@race.io",
}


@pg_only
@pytest.mark.asyncio
async def test_two_concurrent_partial_credit_notes_sum_correctly():
    # pool_size=2: the two credit-note requests below run genuinely
    # concurrently (asyncio.gather + a barrier) and each holds its own
    # connection for the duration of its transaction, so the pool needs
    # capacity for both at once — unlike test_rls_connection_reuse.py's
    # pool_size=1 (which is deliberately forcing SEQUENTIAL reuse of a single
    # connection to prove the WO-27 fix). Still a normal reused pool, not
    # NullPool: connections are checked back in and reused across the whole
    # test (the register/issuer/module/invoice setup calls beforehand).
    engine = create_async_engine(PG_URL, pool_size=2, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _get_test_session():
        async with sm() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    transport = ASGITransport(app=app)
    try:
        # ECB rates aren't seeded by a lifespan under ASGITransport (mirrors conftest._db).
        async with sm() as session:
            await fx.ensure_seed_rates(session, date(2026, 7, 18))
            await fx.ensure_european_coverage(session, date(2026, 7, 18))

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            suffix = uuid.uuid4().hex[:8]
            reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": f"Race Co {suffix}",
                    "name": "Owner",
                    "email": f"owner-{suffix}@race.io",
                    "password": "supersecret",
                },
            )
            assert reg.status_code == 201, reg.text
            client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"

            assert (await client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
            assert (
                await client.put("/api/v1/modules/issuing", json={"enabled": True})
            ).status_code == 200

            # One €1000.00 invoice, 0% VAT, so total == net exactly.
            inv_resp = await client.post(
                "/api/v1/issued",
                json={
                    "buyer_name": "Globex SARL",
                    "buyer_vat_number": "FR40303265045",
                    "issue_date": "2026-05-10",
                    "lines": [
                        {
                            "description": "Consulting",
                            "quantity": "1",
                            "unit_price": "1000.00",
                            "vat_rate": "0",
                        }
                    ],
                },
            )
            assert inv_resp.status_code == 201, inv_resp.text
            inv = inv_resp.json()
            assert inv["total"] == "1000.00"
            invoice_id = inv["id"]

            barrier = asyncio.Barrier(2)

            async def credit(amount: str) -> object:
                await barrier.wait()  # maximize the overlap
                return await client.post(
                    f"/api/v1/issued/{invoice_id}/credit-note",
                    json={
                        "lines": [
                            {
                                "description": "Partial credit",
                                "quantity": "1",
                                "unit_price": amount,
                                "vat_rate": "0",
                            }
                        ]
                    },
                )

            r_a, r_b = await asyncio.gather(credit("300.00"), credit("400.00"))

            # Both must succeed — neither exceeds the invoice total on its own,
            # and (fixed) they must not race each other into a lost update.
            assert r_a.status_code == 201, r_a.text
            assert r_b.status_code == 201, r_b.text

            got = await client.get(f"/api/v1/issued/{invoice_id}")
            assert got.status_code == 200, got.text
            # The one invariant this test exists to prove: the SUM of both
            # concurrent credits, never a lost update (would read back "300.00"
            # or "400.00" instead of "700.00" without the row lock).
            assert Decimal(got.json()["credited_total"]) == Decimal("700.00")
            assert Decimal(got.json()["outstanding"]) == Decimal("300.00")
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
