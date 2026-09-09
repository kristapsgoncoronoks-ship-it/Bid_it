"""DB-020 (audit 2026-09-05, P2 batch 8) — `invoices.vendor_id` is the
composite tenant-safe link on Postgres. Runs where `RLS_TEST_DATABASE_URL`
points at the migrated Postgres the CI `postgres` job provides (and skips on
the SQLite suite, which proves the same refusal with SQLite's enforcement in
`test_db014_db020_vendor_link.py`).

The constraint is named in the refusal so an operator reading the log knows
which promise the database kept.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.models.invoice import Invoice

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a migrated Postgres URL) to run"
)


def _invoice_values(org: str, vendor: str, number: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "org_id": org,
        "vendor_id": vendor,
        "invoice_number": number,
        "issue_date": date(2026, 5, 1),
        "due_date": date(2026, 6, 1),
        "currency": "EUR",
        "subtotal": Decimal("100.00"),
        "tax_amount": Decimal("21.00"),
        "total": Decimal("121.00"),
    }


@pg_only
@pytest.mark.asyncio
async def test_an_invoice_cannot_carry_another_workspaces_vendor():
    engine = create_async_engine(PG_URL)
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    vendor_a = str(uuid.uuid4())
    try:
        async with engine.begin() as conn:
            # Unscoped session (no tenant GUC): the RLS policies admit it, as
            # they do for migrations. Everything below is rolled back.
            for org, name in ((org_a, "Haulage A BV"), (org_b, "Haulage B BV")):
                await conn.execute(
                    text(
                        "INSERT INTO organizations (id, name, plan, status, ai_validation_enabled, "
                        "human_validation_enabled, created_at, updated_at) VALUES "
                        "(:o, :n, 'trial', 'active', true, true, now(), now())"
                    ),
                    {"o": org, "n": name},
                )
            await conn.execute(
                text(
                    "INSERT INTO vendors (id, org_id, name, version, status, created_at, updated_at) "
                    "VALUES (:v, :o, 'Steel GmbH', 1, 'active', now(), now())"
                ),
                {"v": vendor_a, "o": org_a},
            )
            # Positive control: the vendor's own workspace may reference it.
            await conn.execute(
                Invoice.__table__.insert().values(**_invoice_values(org_a, vendor_a, "INV-OWN"))
            )

            await conn.execute(text("SAVEPOINT cross_tenant"))
            with pytest.raises(Exception, match="fk_invoices_vendor"):
                await conn.execute(
                    Invoice.__table__.insert().values(
                        **_invoice_values(org_b, vendor_a, "INV-CROSS")
                    )
                )
            await conn.execute(text("ROLLBACK TO SAVEPOINT cross_tenant"))
            await conn.rollback()
    finally:
        await engine.dispose()
