"""Postgres-only proof that per-issuer invoice numbering is concurrency-safe.

The gap-free, duplicate-free numbering guarantee rests on `issuer.lock()` taking a
`SELECT ... FOR UPDATE` row lock on the issuer entity, so two overlapping issue
transactions on the SAME entity serialize and cannot read the same `next_number`.

That lock is a genuine row lock on Postgres but a no-op on the SQLite test harness
(a single shared connection — no real parallelism), so this test SKIPS on the
default suite and runs in the Postgres CI job via `RLS_TEST_DATABASE_URL`. It fires
many truly-concurrent issue transactions (each its own session/connection, holding
the lock across a deliberate delay so a non-locking implementation would interleave
and collide) and asserts the allocated numbers are exactly 1..N — no gaps, no dupes.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenant import reset_current_org, set_current_org
from app.models.issued_invoice import IssuedInvoice
from app.models.issuer import IssuerProfile
from app.models.organization import Organization
from app.services import issuer as issuer_svc

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL,
    reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the concurrency test",
)

_N = 16  # concurrent issue transactions on the same issuer entity


@pg_only
@pytest.mark.asyncio
async def test_concurrent_numbering_is_gap_free_and_unique_per_issuer():
    # A pool big enough that all workers can hold a connection at once, so they
    # genuinely contend on the issuer row lock rather than queue for a connection.
    engine = create_async_engine(PG_URL, pool_size=_N + 4, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    org_id = str(uuid.uuid4())
    try:
        # Seed the org unscoped (RLS passes when app.current_org is unset), then the
        # issuer entity scoped to the org with its series starting at 1.
        async with sm() as s:
            s.add(Organization(id=org_id, name="Concurrency Co"))
            await s.commit()

        tok = set_current_org(org_id)
        try:
            async with sm() as s:
                prof = IssuerProfile(
                    org_id=org_id,
                    name="CC",
                    is_default=True,
                    legal_name="Concurrency Co BV",
                    invoice_prefix="CC-",
                    next_number=1,
                )
                s.add(prof)
                await s.commit()
                issuer_id = prof.id
        finally:
            reset_current_org(tok)

        async def issue_one() -> int:
            # Each worker runs in its own async task → its own copy of the tenant
            # ContextVar and its own DB connection/transaction.
            tok = set_current_org(org_id)
            try:
                async with sm() as s:
                    prof = await issuer_svc.lock(s, org_id, issuer_id)  # SELECT ... FOR UPDATE
                    n = prof.next_number
                    # Hold the lock across a yield: without FOR UPDATE, other workers
                    # would read this same `n` here and collide.
                    await asyncio.sleep(0.02)
                    prof.next_number = n + 1
                    s.add(
                        IssuedInvoice(
                            org_id=org_id,
                            issuer_id=issuer_id,
                            doc_type="invoice",
                            number=f"CC-2026-{n:04d}",
                            issue_date=date(2026, 5, 10),
                            currency="EUR",
                            buyer_name="Globex",
                            seller_json="{}",
                        )
                    )
                    await s.commit()
                    return n
            finally:
                reset_current_org(tok)

        allocated = await asyncio.gather(*[issue_one() for _ in range(_N)])

        # Gap-free AND duplicate-free: exactly the numbers 1..N, once each.
        assert sorted(allocated) == list(range(1, _N + 1)), allocated
    finally:
        await engine.dispose()
