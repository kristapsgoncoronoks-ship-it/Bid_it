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


@pytest.fixture
def parse_upload(db_session: AsyncSession):
    """Drive the async direct-upload flow (Stage B) end to end: POST the file
    (202 + a run id), drain the queue so the `upload.extract` job parses it on the
    (in-process) worker, then poll the result. Returns the inner ParsedInvoiceDraft
    — shape-identical to the old synchronous /invoices/upload response — so a test
    reads `up["draft"]`, `up["method"]`, `up["fields"]` exactly as before."""

    async def _do(auth_client: AsyncClient, files: dict) -> dict:
        from app.services import jobs

        r = await auth_client.post("/api/v1/invoices/upload", files=files)
        assert r.status_code == 202, r.text
        run_id = r.json()["extraction_run_id"]
        for _ in range(30):  # drain until this upload's parse job has run
            if await jobs.run_once(db_session, "test-worker") is None:
                break
        res = await auth_client.get(f"/api/v1/invoices/upload/{run_id}")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "parsed", body
        return body["draft"]

    return _do


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


@pytest_asyncio.fixture
async def role_client(client: AsyncClient, db_session):
    """Return an async factory: `await role_client("user_free")` -> AsyncClient
    authenticated as a member of a FRESH org whose stored role is that value.
    Stored roles today are UserRole.{user_free,user,admin,owner}; they resolve to
    business roles READ_ONLY/EMPLOYEE/ADMINISTRATOR/OWNER via authz.business_role.

    Registration always creates an OWNER (with the expense-approver flag), so the
    factory downgrades the stored role + flag afterwards — the same pattern
    tests/test_authz.py uses. get_current_user reloads the user per request, so
    the token keeps working with the new role."""
    import uuid

    from sqlalchemy import update

    from app.models.user import User, UserRole

    made: list[AsyncClient] = []

    async def _make(role: str) -> AsyncClient:
        suffix = uuid.uuid4().hex[:8]
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "organization_name": f"RoleOrg-{role}-{suffix}",
                "name": f"Member {role}",
                "email": f"{role}-{suffix}@roleorg.io",
                "password": "supersecret",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        token = body["token"]["access_token"]
        user_id = body["user"]["id"]
        if role != "owner":
            await db_session.execute(
                update(User)
                .where(User.id == user_id)
                .values(role=UserRole(role), is_expense_approver=False)
            )
            await db_session.commit()
        c = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        )
        made.append(c)
        return c

    yield _make
    for c in made:
        await c.aclose()
