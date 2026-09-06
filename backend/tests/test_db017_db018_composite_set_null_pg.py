"""DB-017 / DB-018 (audit 2026-09-05, P2 batch 3) — composite tenant-safe
links detach the RIGHT column. Postgres only: the column-list form
`ON DELETE SET NULL (project_id)` exists from Postgres 15 and has no SQLite
equivalent, so this runs where `RLS_TEST_DATABASE_URL` points at the migrated
Postgres the CI `postgres` job provides (and skips on the SQLite suite).

Before this batch a project delete failed with "null value in column org_id"
(measured, 2026-09-06): the composite SET NULL nulled `org_id` as well as
`project_id`. Now it detaches the documents and leaves the tenant column
intact; the new composite issuer link behaves the same way.
"""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a migrated Postgres URL) to run"
)


@pg_only
@pytest.mark.asyncio
async def test_deleting_a_project_or_an_issuer_detaches_only_its_own_column():
    engine = create_async_engine(PG_URL)
    org = str(uuid.uuid4())
    project = str(uuid.uuid4())
    issuer_id = str(uuid.uuid4())
    invoice = str(uuid.uuid4())
    try:
        async with engine.begin() as conn:
            # Unscoped session (no tenant GUC): the RLS policies admit it, as
            # they do for migrations. Everything below is rolled back.
            await conn.execute(
                text(
                    "INSERT INTO organizations (id, name, plan, status, ai_validation_enabled, "
                    "human_validation_enabled, created_at, updated_at) VALUES "
                    "(:o, 'Site crew BV', 'trial', 'active', true, true, now(), now())"
                ),
                {"o": org},
            )
            await conn.execute(
                text(
                    "INSERT INTO issuer_profiles (id, org_id, is_default, default_currency, "
                    "invoice_prefix, next_number, payment_terms_days, created_at, updated_at) "
                    "VALUES (:i, :o, true, 'EUR', 'INV', 1, 14, now(), now())"
                ),
                {"i": issuer_id, "o": org},
            )
            await conn.execute(
                text(
                    "INSERT INTO projects (id, org_id, code, name, created_at, updated_at) "
                    "VALUES (:p, :o, 'YARD-1', 'Yard extension', now(), now())"
                ),
                {"p": project, "o": org},
            )
            await conn.execute(
                text(
                    "INSERT INTO issued_invoices (id, org_id, issuer_id, project_id, issue_date, "
                    "currency, buyer_name, seller_json, vat_scheme, subtotal, tax_total, total, "
                    "created_at, updated_at) VALUES (:inv, :o, :i, :p, :d, 'EUR', 'Cargo GmbH', "
                    "'{}', 'standard', 100, 21, 121, now(), now())"
                ),
                {"inv": invoice, "o": org, "i": issuer_id, "p": project, "d": date(2026, 5, 1)},
            )

            await conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project})
            row = (
                await conn.execute(
                    text(
                        "SELECT org_id::text, project_id, issuer_id::text FROM issued_invoices "
                        "WHERE id = :inv"
                    ),
                    {"inv": invoice},
                )
            ).one()
            assert row.org_id == org, "DB-018: the tenant column was nulled by a project delete"
            assert row.project_id is None
            assert row.issuer_id == issuer_id

            await conn.execute(text("DELETE FROM issuer_profiles WHERE id = :i"), {"i": issuer_id})
            row = (
                await conn.execute(
                    text("SELECT org_id::text, issuer_id FROM issued_invoices WHERE id = :inv"),
                    {"inv": invoice},
                )
            ).one()
            assert row.org_id == org, "DB-017: the tenant column was nulled by an issuer delete"
            assert row.issuer_id is None

            # An issuer of ANOTHER workspace cannot be referenced at all.
            other_org, other_issuer = str(uuid.uuid4()), str(uuid.uuid4())
            await conn.execute(
                text(
                    "INSERT INTO organizations (id, name, plan, status, ai_validation_enabled, "
                    "human_validation_enabled, created_at, updated_at) VALUES "
                    "(:o, 'Other BV', 'trial', 'active', true, true, now(), now())"
                ),
                {"o": other_org},
            )
            await conn.execute(
                text(
                    "INSERT INTO issuer_profiles (id, org_id, is_default, default_currency, "
                    "invoice_prefix, next_number, payment_terms_days, created_at, updated_at) "
                    "VALUES (:i, :o, true, 'EUR', 'INV', 1, 14, now(), now())"
                ),
                {"i": other_issuer, "o": other_org},
            )
            await conn.execute(text("SAVEPOINT cross_tenant"))
            with pytest.raises(Exception, match="fk_issued_invoices_issuer"):
                await conn.execute(
                    text("UPDATE issued_invoices SET issuer_id = :i WHERE id = :inv"),
                    {"i": other_issuer, "inv": invoice},
                )
            await conn.execute(text("ROLLBACK TO SAVEPOINT cross_tenant"))
            await conn.rollback()
    finally:
        await engine.dispose()
