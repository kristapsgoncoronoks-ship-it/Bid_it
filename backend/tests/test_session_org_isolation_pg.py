"""Session tenant projection through real PostgreSQL RLS, not SQLite stand-ins."""

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from tests.test_session_org_isolation import assert_independent_sessions, headers, pair

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated PostgreSQL")


@pytest.mark.asyncio
async def test_session_projection_and_serialized_switch_precondition_pg():
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = get_test_session
    try:
        async with factory() as db:
            privileged = await db.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
            assert privileged is False
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await assert_independent_sessions(client, db)
                one, two, alpha, beta, uid = await pair(client, db)
                # Both callers reviewed the same source workspace. Only the
                # first switch can consume that precondition under the row lock.
                responses = await asyncio.gather(
                    *[
                        client.post(f"/api/v1/auth/switch-org/{beta}", headers=headers(two, alpha))
                        for _ in range(2)
                    ]
                )
                assert sorted(r.status_code for r in responses) == [200, 409], [
                    r.text for r in responses
                ]
                assert (await client.get("/api/v1/auth/me", headers=headers(one))).json()[
                    "organization"
                ]["id"] == alpha
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
