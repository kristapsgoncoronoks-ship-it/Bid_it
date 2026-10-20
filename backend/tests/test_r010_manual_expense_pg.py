"""Manual expense actor-lock races and atomic rollback on migrated PostgreSQL.

Each race proves concurrent blocked native connections before releasing their
shared actor lock. Queries use independent sessions and scope every count to a
new synthetic organization; SQLite is not a concurrency substitute.
"""

import asyncio
import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from tests.test_r010_manual_expense import manual_payload

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated PostgreSQL")


async def create_owner(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Synthetic manual expense contracts",
            "name": "Synthetic owner",
            "email": f"manual-{uuid4().hex}@example.com",
            "password": "synthetic-test-password",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    client.headers["Authorization"] = "Bearer " + data["token"]["access_token"]
    enabled = await client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    return data["organization"]["id"], data["user"]["id"]


async def assert_native_role(sessions):
    async with sessions() as session:
        assert (
            await session.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
        ) is False


async def committed_counts(sessions, org_id):
    async with sessions() as session:
        counts = []
        for table in ("expense_reports", "expense_items", "expense_commands"):
            counts.append(
                await session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE org_id=:org"), {"org": org_id}
                )
            )
        counts.append(
            await session.scalar(
                text(
                    "SELECT count(*) FROM audit_events WHERE org_id=:org "
                    "AND action='expense.report_created'"
                ),
                {"org": org_id},
            )
        )
        return tuple(counts)


async def wait_for_blocked_actor_commands(sessions, application_name, expected):
    for _ in range(200):
        async with sessions() as session:
            count = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name=:application_name AND wait_event_type='Lock' "
                    "AND query LIKE '%memberships%' AND query LIKE '%FOR UPDATE%'"
                ),
                {"application_name": application_name},
            )
        if count >= expected:
            return
        await asyncio.sleep(0.025)
    raise AssertionError(f"Did not observe {expected} competing native actor-lock waiters")


@pytest.mark.parametrize("intention_mode", ["same_key", "different_keys", "changed_payload"])
async def test_manual_concurrent_commands_preserve_one_outcome_per_intention(intention_mode):
    application_name = f"manual-contract-{uuid4().hex}"
    engine = create_async_engine(
        PG_URL,
        pool_size=6,
        max_overflow=0,
        connect_args={"server_settings": {"application_name": application_name}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    tasks = []
    try:
        await assert_native_role(sessions)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            org_id, user_id = await create_owner(client)
            shared_key = str(uuid4())
            copies = 2 if intention_mode == "changed_payload" else 3
            keys = [
                str(uuid4()) if intention_mode == "different_keys" else shared_key
                for _ in range(copies)
            ]
            bodies = [
                manual_payload(
                    title=f"Synthetic competing intention {i}"
                    if intention_mode == "changed_payload"
                    else "Synthetic manual draft"
                )
                for i in range(copies)
            ]
            async with sessions() as blocker:
                await blocker.execute(
                    text(
                        "SELECT id FROM memberships WHERE org_id=:org AND user_id=:user FOR UPDATE"
                    ),
                    {"org": org_id, "user": user_id},
                )
                tasks = [
                    asyncio.create_task(
                        client.post(
                            "/api/v1/expenses",
                            json=body,
                            headers={"Idempotency-Key": key},
                        )
                    )
                    for key, body in zip(keys, bodies, strict=True)
                ]
                await wait_for_blocked_actor_commands(sessions, application_name, copies)
                assert not any(task.done() for task in tasks)
                assert await committed_counts(sessions, org_id) == (0, 0, 0, 0)
                await blocker.commit()
            responses = await asyncio.gather(*tasks)
            codes = [response.status_code for response in responses]
            if intention_mode == "changed_payload":
                assert sorted(codes) == [201, 409], [response.text for response in responses]
            else:
                assert codes == [201] * copies, [response.text for response in responses]
            saved = [response.json() for response in responses if response.status_code == 201]
            expected = copies if intention_mode == "different_keys" else 1
            assert len({detail["id"] for detail in saved}) == expected
            assert all(detail["total"] == "0.30" for detail in saved)
            assert await committed_counts(sessions, org_id) == (
                expected,
                2 * expected,
                expected,
                expected,
            )
            for key, response in zip(keys, responses, strict=True):
                receipt = await client.get(f"/api/v1/expenses/commands/{key}")
                assert receipt.status_code == 200, receipt.text
                body = receipt.json()
                assert body["status"] == "committed"
                assert body["operation"] == "report_created"
                assert body["command_key"] == key
                assert body["target_id"] == body["result"]["id"]
                assert body["result"] in saved
                if response.status_code == 201:
                    assert body["result"] == response.json()
            async with sessions() as session:
                events = (
                    await session.execute(
                        text(
                            "SELECT target_type,target_id FROM audit_events "
                            "WHERE org_id=:org AND action='expense.report_created'"
                        ),
                        {"org": org_id},
                    )
                ).all()
            assert {tuple(event) for event in events} == {
                ("expense_report", detail["id"]) for detail in saved
            }
            assert await committed_counts(sessions, org_id) == (
                expected,
                2 * expected,
                expected,
                expected,
            )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def test_manual_audit_refusal_rolls_back_all_native_rows_and_original_key_can_retry(
    monkeypatch,
):
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with sessions() as session:
            yield session

    async def refused_audit(*args, **kwargs):
        return None

    app.dependency_overrides[get_session] = get_test_session
    try:
        await assert_native_role(sessions)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            org_id, _ = await create_owner(client)
            key, body = str(uuid4()), manual_payload()
            with monkeypatch.context() as scoped:
                scoped.setattr("app.services.expense_commands.audit.record", refused_audit)
                failure = await client.post(
                    "/api/v1/expenses", json=body, headers={"Idempotency-Key": key}
                )
            assert failure.status_code == 503, failure.text
            assert failure.json()["code"] == "expense_audit_unavailable"
            assert await committed_counts(sessions, org_id) == (0, 0, 0, 0)
            assert (await client.get(f"/api/v1/expenses/commands/{key}")).status_code == 404
            retry = await client.post(
                "/api/v1/expenses", json=body, headers={"Idempotency-Key": key}
            )
            assert retry.status_code == 201, retry.text
            receipt = await client.get(f"/api/v1/expenses/commands/{key}")
            assert receipt.status_code == 200, receipt.text
            assert receipt.json()["result"] == retry.json()
            assert await committed_counts(sessions, org_id) == (1, 2, 1, 1)
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()
