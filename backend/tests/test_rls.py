"""Postgres Row-Level Security backstop (ADR-0004, Phase 2).

RLS is Postgres-only, so these tests SKIP on the default SQLite suite and run when
a Postgres URL is supplied via `RLS_TEST_DATABASE_URL` (a CI job with a Postgres
service, or a local temp cluster). They prove the database itself refuses
cross-tenant rows even for a RAW query that bypasses the ORM tenant guard — the
whole point of the backstop.

They also assert (dialect-independently) that every tenant table is listed in the
RLS migration, so a new tenant table without an RLS policy fails the build — the
same belt-and-braces as the TENANT_MODELS registration guard.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.tenant import TENANT_MODELS

RLS_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(not RLS_URL, reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run RLS tests")


def _rls_migration_tables() -> set[str]:
    """Union `TENANT_TABLES` across every migration that defines it.

    RLS shipped in one migration but later migrations that add a tenant table
    enable RLS on it and declare their own `TENANT_TABLES`; aggregating keeps the
    coverage guard correct as the schema grows (alembic/versions isn't an
    importable package, so each file is loaded by path)."""
    import importlib.util
    from pathlib import Path

    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    tables: set[str] = set()
    for path in sorted(versions.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tables.update(getattr(mod, "TENANT_TABLES", ()))
    return tables


def test_rls_migration_covers_every_tenant_table():
    """The RLS migration's table list must match TENANT_MODELS exactly — so a new
    tenant table can't ship without an RLS policy."""
    model_tables = {m.__tablename__ for m in TENANT_MODELS}
    listed = _rls_migration_tables()
    missing = model_tables - listed
    extra = listed - model_tables
    assert not missing, f"tenant tables missing an RLS policy: {sorted(missing)}"
    assert not extra, f"RLS policy for a non-tenant/renamed table: {sorted(extra)}"


@pg_only
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_raw_query():
    """A RAW SQL query (no ORM guard) sees only the scoped tenant's rows, and
    cannot write into another tenant — enforced by the database, not the app."""
    engine = create_async_engine(RLS_URL)
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        async with engine.begin() as conn:
            for oid, name in ((org_a, "A"), (org_b, "B")):
                await conn.execute(text(
                    "INSERT INTO organizations (id, name, ai_validation_enabled, "
                    "human_validation_enabled, plan, status, created_at, updated_at) "
                    "VALUES (:id, :n, false, false, 'trial', 'active', now(), now())"
                ), {"id": oid, "n": f"Org {name}"})
                await conn.execute(text(
                    "INSERT INTO vendors (id, org_id, name, created_at, updated_at) "
                    "VALUES (:id, :org, :n, now(), now())"
                ), {"id": str(uuid.uuid4()), "org": oid, "n": f"Vendor {name}"})

        # Scoped to A: a raw select returns only A's vendor.
        async with engine.connect() as conn:
            await conn.execute(text("SELECT set_config('app.current_org', :o, false)"), {"o": org_a})
            names = (await conn.execute(text("SELECT name FROM vendors"))).scalars().all()
            assert names == ["Vendor A"], names

            # WITH CHECK blocks writing into another tenant while scoped to A.
            with pytest.raises(Exception):
                await conn.execute(text(
                    "INSERT INTO vendors (id, org_id, name, created_at, updated_at) "
                    "VALUES (:id, :org, 'Sneaky', now(), now())"
                ), {"id": str(uuid.uuid4()), "org": org_b})

        # Scoped to B: only B's vendor.
        async with engine.connect() as conn:
            await conn.execute(text("SELECT set_config('app.current_org', :o, false)"), {"o": org_b})
            names = (await conn.execute(text("SELECT name FROM vendors"))).scalars().all()
            assert names == ["Vendor B"], names
    finally:
        await engine.dispose()
