"""FS16-01: reviewed publication serializes in migrated PostgreSQL."""

import asyncio
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from app.models.automation import AutomationRuleVersion
from tests.test_automation import STALE_RULE

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")


@pytest.mark.asyncio
async def test_concurrent_publish_and_edit_have_one_reviewed_winner():
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def session():
        async with factory() as db:
            yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = session
    try:
        async with factory() as db:
            assert (
                await db.scalar(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
                is False
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic published rules",
                    "name": "Synthetic Owner",
                    "email": f"rules-{uuid.uuid4().hex}@example.com",
                    "password": "Synthetic-Postgres-Rule-Password-32",
                },
            )
            assert registration.status_code == 201
            client.headers["Authorization"] = (
                "Bearer " + registration.json()["token"]["access_token"]
            )
            created = await client.post("/api/v1/automation/rules", json=STALE_RULE)
            assert created.status_code == 201
            row = created.json()
            path = f"/api/v1/automation/rules/{row['id']}"
            headers = {"If-Match": row["revision"]}
            responses = await asyncio.gather(
                *[client.post(path + "/publish", headers=headers) for _ in range(2)]
            )
            assert sorted(r.status_code for r in responses) == [200, 409]
            fresh = (await client.get("/api/v1/automation/rules")).json()[0]
            assert fresh["published_version"] == 1
            headers = {"If-Match": fresh["revision"]}
            responses = await asyncio.gather(
                client.patch(path, headers=headers, json={"name": "Concurrent reviewed edit"}),
                client.post(path + "/publish", headers=headers),
            )
            assert sorted(r.status_code for r in responses) == [200, 409]
            async with factory() as db:
                versions = await db.scalar(
                    select(func.count())
                    .select_from(AutomationRuleVersion)
                    .where(AutomationRuleVersion.rule_id == row["id"])
                )
                assert versions == (2 if responses[1].status_code == 200 else 1)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
