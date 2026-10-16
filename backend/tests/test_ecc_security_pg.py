"""Non-superuser PostgreSQL proof for OIDC consumption and calendar tenant access."""

import asyncio
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from app.models.organization import Organization
from app.models.sso import SsoConnection
from app.models.user import UserRole
from app.services import memberships, scim, sso_transactions
from tests.test_session_org_isolation import headers, pair

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated PostgreSQL")


@pytest.mark.asyncio
async def test_one_use_consumption_and_calendar_offboarding_pg():
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = get_test_session
    try:
        async with factory() as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                    )
                )
                is False
            )
            org = Organization(name="Synthetic OIDC race")
            db.add(org)
            await db.flush()
            org_id = org.id
            state = uuid.uuid4().hex
            secret = await sso_transactions.start(db, org_id, state)
        barrier = asyncio.Barrier(3)

        async def consume():
            async with factory() as db:
                await asyncio.wait_for(barrier.wait(), timeout=10)
                return await sso_transactions.consume(db, org_id, state, secret)

        results = await asyncio.gather(*[consume() for _ in range(3)])
        assert sorted(results) == [False, False, True]
        async with factory() as db:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                one, two, alpha, beta, uid = await pair(client, db)
                original = await client.get("/api/v1/schedule/feed-token", headers=headers(one))
                path_a = original.json()["path"]
                assert (
                    await client.post(f"/api/v1/auth/switch-org/{beta}", headers=headers(two))
                ).status_code == 200
                path_b = (
                    await client.get("/api/v1/schedule/feed-token", headers=headers(two))
                ).json()["path"]
                assert (await client.get(path_a)).status_code == 200
                assert (await client.get(path_b)).status_code == 200
                await memberships.set_status(db, alpha, uid, "suspended")
                await db.commit()
                assert (await client.get(path_a)).status_code == 404
                assert (await client.get(path_b)).status_code == 200
                await memberships.set_status(db, alpha, uid, "active")
                await db.commit()
                assert (await client.get(path_a)).status_code == 404
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_scim_offboarding_preserves_independent_org_session_pg():
    engine = create_async_engine(PG_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = get_test_session
    try:
        async with factory() as db:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                owner, _, alpha, beta, _ = await pair(client, db)
                email = f"pg-offboard-{uuid.uuid4().hex}@example.com"
                invite = await client.post(
                    "/api/v1/team/invites",
                    json={"email": email, "role": "user"},
                    headers=headers(owner),
                )
                accepted = await client.post(
                    "/api/v1/auth/accept-invite",
                    json={
                        "token": invite.json()["token"],
                        "name": "Synthetic Staff",
                        "password": "synthetic-password",
                    },
                )
                assert accepted.status_code == 201
                user = accepted.json()["user"]["id"]
                one = accepted.json()["token"]["access_token"]
                await memberships.ensure(db, org_id=beta, user_id=user, role=UserRole.user)
                conn = SsoConnection(org_id=alpha, slug=f"scim-{uuid.uuid4().hex}", protocol="oidc")
                db.add(conn)
                await db.commit()
                machine = await scim.set_token(db, alpha)
                login = await client.post(
                    "/api/v1/auth/login", json={"email": email, "password": "synthetic-password"}
                )
                two = login.json()["token"]["access_token"]
                assert (
                    await client.post(f"/api/v1/auth/switch-org/{beta}", headers=headers(two))
                ).status_code == 200
                a_path = (
                    await client.get("/api/v1/schedule/feed-token", headers=headers(one))
                ).json()["path"]
                b_path = (
                    await client.get("/api/v1/schedule/feed-token", headers=headers(two))
                ).json()["path"]
                response = await client.patch(
                    f"/api/v1/scim/v2/Users/{user}",
                    headers={"Authorization": f"Bearer {machine}"},
                    json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
                )
                assert response.status_code == 200 and response.json()["active"] is False
                recovered = await client.post(
                    "/api/v1/auth/login",
                    json={"email": email, "password": "synthetic-password"},
                )
                assert recovered.status_code == 200
                assert recovered.json()["organization"]["id"] == beta
                assert (
                    await client.get("/api/v1/auth/me", headers=headers(one))
                ).status_code == 401
                assert (
                    await client.get("/api/v1/auth/me", headers=headers(two))
                ).status_code == 200
                assert (await client.get(a_path)).status_code == 404
                assert (await client.get(b_path)).status_code == 200
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
