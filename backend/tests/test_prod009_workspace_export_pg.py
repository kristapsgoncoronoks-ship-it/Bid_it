"""PROD-009 (P2 batch 9) — the whole-workspace export on real Postgres, where
FORCE ROW LEVEL SECURITY is the isolation layer the SQLite suite cannot have.

The export walks 100-odd tables and writes every row it is handed to a file the
customer downloads. That makes it the one read in the product where a missing
tenant predicate is not a leak into a screen but a leak into a ZIP someone
keeps. The SQLite suite proves the export's SHAPE and its redaction; only
Postgres can prove that the third isolation layer is what it claims to be while
the export runs.

Runs where `RLS_TEST_DATABASE_URL` points at the migrated Postgres the CI
`postgres` job provides, as a NON-superuser role (RLS does not apply to a
superuser, which is why that job creates `appuser` with NOSUPERUSER).
"""

from __future__ import annotations

import io
import json
import os
import uuid
import zipfile

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenant import reset_current_org, set_current_org
from app.services import workspace_export

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a migrated Postgres URL) to run"
)


async def _seed_org(conn, name: str) -> tuple[str, str]:
    """One organization with one vendor. Returns (org_id, vendor_name)."""
    org = str(uuid.uuid4())
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
            "VALUES (:v, :o, :n, 1, 'active', now(), now())"
        ),
        {"v": str(uuid.uuid4()), "o": org, "n": f"{name} Supplier OU"},
    )
    return org, f"{name} Supplier OU"


@pg_only
@pytest.mark.asyncio
async def test_the_export_carries_one_tenants_rows_and_no_other_tenants():
    """Two workspaces, each with a distinctly-named supplier. Export the first
    inside its own tenant context — the way the worker runs it — and read the
    ZIP: the other workspace's name must not appear anywhere in it, not in the
    vendors file and not in any of the other hundred."""
    engine = create_async_engine(PG_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    org_a = org_b = None
    name_a = name_b = ""
    try:
        async with engine.begin() as conn:
            org_a, name_a = await _seed_org(conn, "Northwind Alpha")
            org_b, name_b = await _seed_org(conn, "Southgate Beta")

        token = set_current_org(org_a)
        try:
            async with session_factory() as db:
                await db.execute(
                    text("SELECT set_config('app.current_org', :o, false)"), {"o": org_a}
                )
                path, summary = await workspace_export.build_zip_file(db, org_a)
        finally:
            reset_current_org(token)

        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        finally:
            os.unlink(path)

        zf = zipfile.ZipFile(io.BytesIO(raw))
        blob = b"".join(zf.read(n) for n in zf.namelist())
        assert name_a.encode() in blob, "the export lost its own workspace's supplier"
        assert name_b.encode() not in blob, "another workspace's supplier reached the export"
        assert org_b.encode() not in blob, "another workspace's id reached the export"
        vendors = [
            json.loads(line) for line in zf.read("data/vendors.jsonl").decode().splitlines() if line
        ]
        assert [v["name"] for v in vendors] == [name_a]
        assert summary["workspace_id"] == org_a

        # --- and now with layer 3 standing down -----------------------------
        # The run above passes even if the export's own `org_id` filter is
        # deleted, because RLS catches it — which is the point of that half, and
        # the reason it cannot also prove the application layer. So: run it
        # again from a session with NO tenant context at all. The RLS policy
        # admits every row when `app.current_org` is unset (the same latitude
        # migrations need) and the ORM guard scopes nothing without a context,
        # so what scopes this second export is ONLY the module's own predicate.
        # Delete that predicate and this half goes red.
        async with session_factory() as db:
            path2, _ = await workspace_export.build_zip_file(db, org_a)
        try:
            with open(path2, "rb") as fh:
                raw2 = fh.read()
        finally:
            os.unlink(path2)
        zf2 = zipfile.ZipFile(io.BytesIO(raw2))
        unguarded = b"".join(zf2.read(n) for n in zf2.namelist())
        assert name_a.encode() in unguarded, "the unguarded export lost its own rows"
        assert name_b.encode() not in unguarded, (
            "with RLS standing down, the export's own tenant filter did not hold"
        )
    finally:
        async with engine.begin() as conn:
            for org in (o for o in (org_a, org_b) if o):
                await conn.execute(text("DELETE FROM vendors WHERE org_id = :o"), {"o": org})
                await conn.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org})
        await engine.dispose()
