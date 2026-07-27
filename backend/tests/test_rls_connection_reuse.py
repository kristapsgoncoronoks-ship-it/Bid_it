"""Postgres-only regression coverage for WO-27: the RLS "unscoped" GUC losing its
NULL identity after the first scoped transaction on a REUSED connection.

Background (see docs/architecture/adr/0023-rls-unscoped-guc-sticky-empty-string.md):
every RLS policy in this codebase reads `current_setting('app.current_org', true)
IS NULL` as "the app is running unscoped (bootstrap / platform-operator /
worker-claim) — bypass". That is only true on a virgin backend connection. Once
`set_config('app.current_org', :org, true)` has run on a physical connection —
even once, even for a legitimately-scoped request — `current_setting(name, true)`
never returns SQL NULL again for that connection's life; it returns `''`
instead (COMMIT, RESET, and an explicit `set_config(name, NULL, true)` all leave
it at `''`, confirmed empirically). Two distinct failure modes follow, on ANY
connection pool that reuses connections across requests (i.e. every real
deployment, and every HTTP test that doesn't use `NullPool`):

  1. Re-authentication hides real users: `get_current_identity`
     (app/api/deps.py `_authenticate`) does its FIRST query — `db.get(User,
     payload["sub"])` — deliberately UNSCOPED (the org isn't known yet). On a
     connection a PRIOR request already scoped, that lookup now sees zero rows
     under RLS, so the row comes back `None` and the request 401s — a valid
     token intermittently rejected depending only on which physical connection
     the pool hands back.
  2. Genuinely-unscoped paths (bootstrap, platform-operator dashboards,
     worker-claim queries) lose visibility the same way, on any connection a
     scoped request warmed first.

`test_two_sequential_authenticated_requests_on_reused_connection` proves (1);
`test_unscoped_query_after_scoped_request_sees_all_rows` proves (2). Both use a
SMALL pool (`pool_size=1, max_overflow=0`) to force physical-connection reuse
across sequential (not concurrent) operations — deliberately the OPPOSITE of
`test_credit_note_lock_concurrency.py`'s `NullPool` workaround, which sidesteps
this bug rather than exercising a fix for it.
"""

from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.core.tenant import reset_current_org, set_current_org
from app.main import app
from app.models.organization import Organization
from app.models.vendor import Vendor

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not PG_URL, reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run RLS tests"
)


@pg_only
@pytest.mark.asyncio
async def test_two_sequential_authenticated_requests_on_reused_connection():
    """Register, then fire TWO sequential authenticated GET /auth/me calls on a
    pool_size=1 engine (forces the second call's connection to be the one the
    first call's request already scoped). The first call warms the connection's
    GUC to the real org id; the second call's `get_current_identity` does its
    unscoped `db.get(User, ...)` lookup on that now-warmed connection. Pre-fix
    this 401s ("Could not validate credentials") even though the token is
    perfectly valid — this is the direct regression test for that.
    """
    engine = create_async_engine(PG_URL, pool_size=1, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _get_test_session():
        async with sm() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            suffix = uuid.uuid4().hex[:8]
            reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": f"Reuse Co {suffix}",
                    "name": "Owner",
                    "email": f"owner-{suffix}@reuse.io",
                    "password": "supersecret",
                },
            )
            assert reg.status_code == 201, reg.text
            token = reg.json()["token"]["access_token"]
            client.headers["Authorization"] = f"Bearer {token}"

            # Authenticated request #1: warms the sole pooled connection's GUC
            # to this org (via apply_db_tenant inside get_current_identity).
            first = await client.get("/api/v1/auth/me")
            assert first.status_code == 200, first.text

            # Authenticated request #2: reuses the SAME physical connection
            # (pool_size=1). This is the one that must not spuriously 401.
            second = await client.get("/api/v1/auth/me")
            assert second.status_code == 200, second.text
            assert second.json()["user"]["email"] == f"owner-{suffix}@reuse.io"

            # And a third, fourth… — not a one-off fluke of ordering.
            for _ in range(3):
                again = await client.get("/api/v1/auth/me")
                assert again.status_code == 200, again.text
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pg_only
@pytest.mark.asyncio
async def test_unscoped_query_after_scoped_request_sees_all_rows():
    """A genuinely-unscoped query (org=None throughout — the worker-claim /
    platform-operator shape) run AFTER a scoped request on the same pool_size=1
    engine must still see every org's rows, not zero. Exercises the real
    app.core.tenant machinery (`_sync_rls_org` / `set_current_org`), not a raw
    SQL simulation, so it proves the actual code path.
    """
    engine = create_async_engine(PG_URL, pool_size=1, max_overflow=0)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        # Seed step: unscoped (context var default None) — mirrors bootstrap.
        async with sm() as session:
            for oid, name in ((org_a, "A"), (org_b, "B")):
                session.add(
                    Organization(
                        id=oid,
                        name=f"Org {name}",
                        ai_validation_enabled=False,
                        human_validation_enabled=False,
                        plan="trial",
                        status="active",
                    )
                )
                session.add(Vendor(id=str(uuid.uuid4()), org_id=oid, name=f"Vendor {name}"))
            await session.commit()

        # Scoped step: a normal authenticated-request-shaped transaction,
        # scoped to org A. Warms the sole pooled connection's GUC to org_a.
        token = set_current_org(org_a)
        try:
            async with sm() as session:
                rows = (await session.execute(select(Vendor.name))).scalars().all()
                assert rows == ["Vendor A"], rows
        finally:
            reset_current_org(token)

        # Unscoped step: the worker-claim / platform-operator shape — context
        # var back to None, exactly like TenantScopeMiddleware between
        # requests. Must see BOTH vendors, not zero (today's bug hides them).
        # Scoped down to just these two seeded org ids (an explicit app-level
        # WHERE, not a GUC filter) so the assertion is exact even though the
        # RLS-scratch database accumulates rows across separate test runs;
        # RLS itself must still let BOTH through since the query is unscoped.
        async with sm() as session:
            names = sorted(
                (
                    await session.execute(
                        text("SELECT name FROM vendors WHERE org_id::text = ANY(:orgs)"),
                        {"orgs": [org_a, org_b]},
                    )
                )
                .scalars()
                .all()
            )
            assert names == ["Vendor A", "Vendor B"], names
    finally:
        await engine.dispose()
