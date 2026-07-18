from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import app
from app.models import Base


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    # Fresh in-memory DB per test; StaticPool keeps the single connection alive.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ASGITransport doesn't run the app lifespan, so seed the bundled ECB rates
    # here (mirrors what startup does) so FX conversion works in tests.
    from datetime import date

    from app.services import fx

    async with testing_session() as session:
        await fx.ensure_seed_rates(session, date(2026, 7, 18))

    async def _get_test_session():
        async with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient) -> AsyncClient:
    """A client with a registered org + bearer token already attached."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Acme",
            "name": "Owner",
            "email": "owner@acme.io",
            "password": "supersecret",
        },
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
