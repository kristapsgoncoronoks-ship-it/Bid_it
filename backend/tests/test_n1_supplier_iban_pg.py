"""Received bank evidence through PostgreSQL tenant policies and physical columns."""

import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from tests.test_n1_supplier_iban import (
    test_snapshot_review_clear_and_vendor_bank_approval_independence as exercise_snapshot,
)

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated PostgreSQL")


async def test_supplier_iban_postgres_storage_and_controlled_vendor_independence(caplog):
    engine = create_async_engine(PG_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        async with (
            factory() as db,
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            assert (
                await db.scalar(
                    text(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                    )
                )
                is False
            )
            result = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic PG Bank",
                    "name": "Bank Owner",
                    "email": f"bank-{uuid4().hex}@example.com",
                    "password": "supersecret",
                },
            )
            assert result.status_code == 201
            client.headers["Authorization"] = "Bearer " + result.json()["token"]["access_token"]
            await exercise_snapshot(client, db, caplog)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
