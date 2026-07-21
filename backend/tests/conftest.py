from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import app
from app.models import Base


@pytest_asyncio.fixture
async def _db():
    """A fresh in-memory DB per test, shared by the HTTP client and db_session."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ASGITransport doesn't run the app lifespan, so seed the bundled ECB rates.
    from app.services import fx

    async with sm() as session:
        await fx.ensure_seed_rates(session, date(2026, 7, 18))
        await fx.ensure_european_coverage(session, date(2026, 7, 18))

    yield sm
    await engine.dispose()


@pytest.fixture(autouse=True)
def _storage():
    """Give every test an isolated in-memory object-storage backend (no disk)."""
    from app.core import storage

    storage.set_storage(storage.MemoryStorage())
    yield
    storage.reset_storage()


@pytest.fixture(autouse=True)
def _ratelimit():
    """Rate-limit counters are process-global; clear them around every test so
    one test's request volume can't spill into another's (order-independent)."""
    from app.core import ratelimit

    ratelimit.reset_all()
    yield
    ratelimit.reset_all()


@pytest_asyncio.fixture
async def client(_db) -> AsyncGenerator[AsyncClient, None]:
    async def _get_test_session():
        async with _db() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def db_session(_db) -> AsyncGenerator[AsyncSession, None]:
    """A direct session on the same DB the client uses (for test-only setup)."""
    async with _db() as session:
        yield session


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
