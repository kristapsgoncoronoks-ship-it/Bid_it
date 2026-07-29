"""Postgres-only proof that the `vat_claimed_invoices` one-invoice-one-
submission lock (R4/R5, WO-51/G2.2) is genuinely concurrency-safe.

`app.services.transport.lock.submit_claim` acquires one lock row per invoice
via a plain ORM INSERT (never an upsert) in the SAME flush as the claim's
`status` mutation. That "a lost race raises IntegrityError and rolls back the
WHOLE transition" guarantee is only real under true concurrent writers — two
separate connections/transactions genuinely racing the same UNIQUE constraint
— which SQLite's single-shared-connection test harness cannot exercise
meaningfully (mirrors `tests/test_numbering_concurrency.py`,
`tests/test_credit_note_lock_concurrency.py`). This test SKIPS on the default
suite and runs in the Postgres CI job via `RLS_TEST_DATABASE_URL`.

Each racer runs in its OWN asyncio task with its OWN session/connection (not
the same shared `db_session` fixture the SQLite service tests use), fired via
`asyncio.gather` behind a barrier to maximize the overlap — the realistic
"two operators tried to claim the same underlying purchase" scenario the WO
calls for: two DIFFERENT `VatRefundClaim` rows (same entity/refund_country,
distinct `ref_period` so the claim GRAIN itself doesn't collide) race to lock
an overlapping invoice key pointing at the SAME `FuelTransaction` anchor row.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenant import reset_current_org, set_current_org
from app.models.issuer import IssuerProfile
from app.models.organization import Organization
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.vat_claim import VatRefundClaim
from app.services import modules
from app.services.transport import lock

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the concurrency test"
)

_INVOICE_REF = "INV-RACE-0001"
_SUPPLIER = "Q8"


async def _seed(sm, org_name: str) -> dict[str, str]:
    """Seed one org with `transport` enabled, one entity, one anchor
    FuelTransaction row, and TWO draft claims (distinct ref_period, same
    entity/refund_country) — returns the ids the racers need."""
    org_id = str(uuid.uuid4())
    async with sm() as s:
        s.add(Organization(id=org_id, name=org_name))
        await s.commit()

    tok = set_current_org(org_id)
    try:
        async with sm() as s:
            await modules.set_enabled(s, org_id, "transport", True)
            entity = IssuerProfile(
                org_id=org_id,
                name="LR",
                is_default=True,
                legal_name=f"{org_name} BV",
                invoice_prefix="LR-",
                next_number=1,
                country="LV",
            )
            s.add(entity)
            await s.flush()

            txn = FuelTransaction(
                org_id=org_id,
                entity_id=entity.id,
                supplier=_SUPPLIER,
                period="2026-06",
                line_seq=1,
                country="LV",
                vehicle_ref="Fleet-Race-01",
                txn_date=date(2026, 6, 15),
                station="Demo Station Riga",
                product="DIESEL",
                product_group="Diesel",
                qty=Decimal("100.000"),
                currency="EUR",
                net_local=Decimal("100.00"),
                vat_local=Decimal("21.00"),
                gross_local=Decimal("121.00"),
                net_eur=Decimal("100.00"),
                vat_eur=Decimal("21.00"),
                net_eur_eff=Decimal("100.00"),
                invoice_ref=_INVOICE_REF,
            )
            s.add(txn)
            await s.flush()

            claim_a = VatRefundClaim(
                org_id=org_id, entity_id=entity.id, refund_country="LV", ref_period="2026-Q1"
            )
            claim_b = VatRefundClaim(
                org_id=org_id, entity_id=entity.id, refund_country="LV", ref_period="2026-Q2"
            )
            s.add(claim_a)
            s.add(claim_b)
            await s.commit()

            return {
                "org_id": org_id,
                "entity_id": entity.id,
                "txn_id": txn.id,
                "claim_a_id": claim_a.id,
                "claim_b_id": claim_b.id,
            }
    finally:
        reset_current_org(tok)


@pg_only
@pytest.mark.asyncio
async def test_two_concurrent_submissions_over_the_same_invoice_exactly_one_wins():
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        ids = await _seed(sm, "Lock Race Co")
        org_id = ids["org_id"]
        txn_id = ids["txn_id"]

        barrier = asyncio.Barrier(2)

        async def _submit(claim_id: str) -> str:
            # Each worker runs in its own async task -> its own copy of the
            # tenant ContextVar and its own DB connection/transaction.
            tok = set_current_org(org_id)
            try:
                async with sm() as s:
                    await barrier.wait()  # maximize the overlap
                    try:
                        await lock.submit_claim(
                            s,
                            org_id,
                            claim_id=claim_id,
                            invoices=[(_SUPPLIER, _INVOICE_REF, txn_id)],
                        )
                        await s.commit()
                        return "won"
                    except IntegrityError:
                        await s.rollback()
                        return "lost"
            finally:
                reset_current_org(tok)

        r_a, r_b = await asyncio.gather(_submit(ids["claim_a_id"]), _submit(ids["claim_b_id"]))

        # Exactly one succeeds; the loser's status is unchanged.
        assert sorted([r_a, r_b]) == ["lost", "won"]

        tok = set_current_org(org_id)
        try:
            async with sm() as s:
                locks = (
                    await s.scalars(
                        select(VatClaimedInvoice).where(VatClaimedInvoice.org_id == org_id)
                    )
                ).all()
                # Exactly one VatClaimedInvoice row exists for that natural key.
                assert len(locks) == 1

                fresh_a = await s.scalar(
                    select(VatRefundClaim).where(VatRefundClaim.id == ids["claim_a_id"])
                )
                fresh_b = await s.scalar(
                    select(VatRefundClaim).where(VatRefundClaim.id == ids["claim_b_id"])
                )
                statuses = [fresh_a.status, fresh_b.status]
                # Exactly one claim reads "submitted" from a FRESH query; the
                # OTHER reads its original pre-submission status ("draft")
                # from a fresh query — proving the whole transaction rolled
                # back, not just the lock insert.
                assert sorted(statuses) == ["draft", "submitted"]
        finally:
            reset_current_org(tok)
    finally:
        await engine.dispose()


@pg_only
@pytest.mark.asyncio
async def test_withdraw_then_reclaim_by_a_different_claim_succeeds():
    """After `withdraw_claim` on the winner, a THIRD claim can `submit_claim`
    the same invoice key — R5's 'after withdraw, it can' acceptance line,
    proven on real Postgres (not just SQLite), where the previous test
    already proved the opposite ('cannot, while locked') on the same
    infrastructure."""
    engine = create_async_engine(PG_URL, pool_size=2, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        ids = await _seed(sm, "Withdraw Reclaim Co")
        org_id = ids["org_id"]
        txn_id = ids["txn_id"]

        tok = set_current_org(org_id)
        try:
            async with sm() as s:
                await lock.submit_claim(
                    s,
                    org_id,
                    claim_id=ids["claim_a_id"],
                    invoices=[(_SUPPLIER, _INVOICE_REF, txn_id)],
                )
                await s.commit()

            async with sm() as s:
                await lock.withdraw_claim(s, org_id, ids["claim_a_id"])
                await s.commit()

            async with sm() as s:
                entity_id = ids["entity_id"]
                claim_c = VatRefundClaim(
                    org_id=org_id, entity_id=entity_id, refund_country="LV", ref_period="2026-Q3"
                )
                s.add(claim_c)
                await s.commit()
                claim_c_id = claim_c.id

            async with sm() as s:
                result = await lock.submit_claim(
                    s, org_id, claim_id=claim_c_id, invoices=[(_SUPPLIER, _INVOICE_REF, txn_id)]
                )
                await s.commit()
                assert result.status == "submitted"

            async with sm() as s:
                locks = (
                    await s.scalars(
                        select(VatClaimedInvoice).where(VatClaimedInvoice.org_id == org_id)
                    )
                ).all()
                assert len(locks) == 1
                assert locks[0].claim_id == claim_c_id
        finally:
            reset_current_org(tok)
    finally:
        await engine.dispose()
