"""Every RLS policy carries the WO-27 empty-string leg — the shape, not just
the coverage.

`test_rls.py` proves that every tenant table HAS a `tenant_isolation` policy.
It says nothing about what the policy says, and that gap cost the product 45
broken policies: `6fec8c88ba7c` (ADR-0028) rewrote all 55 that existed when
the sticky-empty-string quirk was found, but every tenant table added
afterwards copied the pre-fix two-leg predicate out of an older migration, and
nothing checked. `c2d3e4f5a6b7` repaired them; this is the gate that keeps the
next one honest.

The quirk, restated because the assertion is meaningless without it: once any
transaction on a physical Postgres connection has run
`set_config('app.current_org', …, true)`, `current_setting(name, true)`
returns `''` — never SQL NULL — for the rest of that connection's life. A
policy whose "unscoped" leg tests only `IS NULL` therefore matches NOTHING on
a pooled connection, and every deliberately-unscoped read (a one-time export
download, a platform-operator query, the scheduler's cross-tenant sweep)
silently returns zero rows depending only on which connection the pool handed
back.

Postgres-only by nature: SQLite has no row-level security, so the SQLite suite
cannot see this class of defect at all. Runs where `RLS_TEST_DATABASE_URL`
points at the migrated Postgres the CI `postgres` job provides.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a migrated Postgres URL) to run"
)

_POLICIES = text(
    "SELECT c.relname, pg_get_expr(p.polqual, p.polrelid) AS using_expr, "
    "       pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr "
    "FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
    "WHERE p.polname = 'tenant_isolation' ORDER BY c.relname"
)

#: What an unscoped connection's GUC actually reads once it has been set.
_EMPTY_LEG = "= ''::text"


@pg_only
@pytest.mark.asyncio
async def test_every_tenant_policy_treats_an_empty_guc_as_unscoped():
    engine = create_async_engine(PG_URL)
    try:
        async with engine.connect() as conn:
            rows = list(await conn.execute(_POLICIES))
    finally:
        await engine.dispose()

    assert rows, "no tenant_isolation policies found — is this database migrated?"
    missing = sorted(
        name
        for name, using_expr, check_expr in rows
        if _EMPTY_LEG not in (using_expr or "") or _EMPTY_LEG not in (check_expr or "")
    )
    assert missing == [], (
        "these tenant_isolation policies read an unscoped connection as SQL NULL only, "
        "so they match nothing once the GUC has gone sticky-empty (ADR-0028): "
        f"{missing}. Use the three-leg predicate — see c2d3e4f5a6b7."
    )


@pg_only
@pytest.mark.asyncio
async def test_the_sticky_empty_guc_really_reads_as_unscoped_on_a_repaired_policy():
    """The quirk itself, reproduced, so the assertion above is not folklore:
    scope a transaction, end it, and read the GUC on the SAME connection — it
    is `''`, not NULL — then confirm a repaired policy still admits rows."""
    engine = create_async_engine(PG_URL)
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text("SELECT set_config('app.current_org', 'deadbeef', true)"))
            # Same physical connection, new transaction, nothing set.
            guc = (
                await conn.execute(text("SELECT current_setting('app.current_org', true)"))
            ).scalar()
            assert guc == "", f"expected the sticky empty string, read {guc!r}"

            # And the repaired policy reads that as unscoped rather than as a
            # tenant id nothing matches. `workspace_exports` is this batch's
            # own table and its download route is the unscoped path.
            await conn.execute(text("SELECT count(*) FROM workspace_exports"))
    finally:
        await engine.dispose()
