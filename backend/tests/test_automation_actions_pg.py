"""FP-P02 real commits, native worker claim, frozen message and access recheck."""

import asyncio
import json
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_session
from app.main import app
from app.models.automation import AutomationRun
from app.models.crm import CustomerNote
from app.models.email_message import EmailMessage
from app.models.membership import Membership
from app.services import automation, automation_actions, jobs, mailer
from tests.automation_client import reviewed_request

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")


@pytest.mark.asyncio
async def test_native_worker_commit_offboarding_resume_and_completed_note(monkeypatch):
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def session():
        async with factory() as db:
            yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = session
    monkeypatch.setattr(settings, "smtp_host", "synthetic-relay.test")
    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: None)
    try:
        async with factory() as db:
            assert (
                await db.scalar(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
                is False
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic actions PG",
                    "name": "Synthetic Owner",
                    "email": f"actions-{uuid.uuid4().hex}@example.com",
                    "password": "Synthetic-Actions-Postgres-Password-32",
                },
            )
            assert response.status_code == 201
            client.headers["Authorization"] = "Bearer " + response.json()["token"]["access_token"]
            identity = (await client.get("/api/v1/auth/me")).json()
            org_id = identity["organization"]["id"]
            assert (
                await client.put("/api/v1/modules/issuing", json={"enabled": True})
            ).status_code == 200
            customer = await client.post(
                "/api/v1/customers", json={"name": "Synthetic PG customer"}
            )
            assert customer.status_code == 201
            definition = {
                "name": "Synthetic frozen customer actions",
                "trigger": "customer.dormant",
                "condition": None,
                "actions": [
                    {"kind": "create_customer_note", "body": "Exactly one synthetic note"},
                    {
                        "kind": "notify_owner_email",
                        "subject": "Frozen",
                        "body": "Customer {{customer_name}}",
                    },
                ],
            }
            row = (await client.post("/api/v1/automation/rules", json=definition)).json()
            await reviewed_request(client, "post", f"/api/v1/automation/rules/{row['id']}/publish")
            async with factory() as db:
                result = await automation.sweep(db, org_id)
                assert result["pending"] == 1 and result["fired"] == 0
                run = await db.scalar(
                    select(AutomationRun).where(AutomationRun.rule_id == row["id"])
                )
                run_id = run.id
                await db.commit()
                message_id = json.loads(run.detail_json)["actions"][1]["message_id"]
                await db.execute(
                    update(Membership).where(Membership.org_id == org_id).values(status="suspended")
                )
                await db.commit()
                monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
                job = await jobs.run_once(
                    db, "synthetic-actions-worker", kinds=[automation_actions.KIND]
                )
                assert job.status == "succeeded" and calls == []
                await db.refresh(run)
                assert run.status == "blocked"
                await db.execute(
                    update(Membership).where(Membership.org_id == org_id).values(status="active")
                )
                await db.commit()

            async def inspect_committed():
                probe = create_async_engine(PG_URL)
                async with async_sessionmaker(probe)() as independent:
                    message = await independent.get(EmailMessage, message_id)
                    assert (
                        message.status == "sending"
                        and message.body == "Customer Synthetic PG customer"
                    )
                    assert (
                        await independent.scalar(
                            select(func.count())
                            .select_from(CustomerNote)
                            .where(CustomerNote.org_id == org_id)
                        )
                        == 1
                    )
                await probe.dispose()

            def accepted(*args):
                asyncio.run(inspect_committed())
                calls.append(args)

            monkeypatch.setattr(mailer, "_smtp_send", accepted)
            # Explicitly resume the same frozen occurrence; completed note stays done.
            for _ in range(2):
                retry = await client.post(f"/api/v1/automation/runs/{run_id}/retry")
                assert retry.status_code == 200 and retry.json()["status"] == "pending"
            async with factory() as db:
                job = await jobs.run_once(
                    db, "synthetic-actions-worker", kinds=[automation_actions.KIND]
                )
                assert job.status == "succeeded" and len(calls) == 1
                assert (await db.get(AutomationRun, run_id)).status == "ok"
                assert (await db.get(EmailMessage, message_id)).sent_at is not None
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(CustomerNote)
                        .where(CustomerNote.org_id == org_id)
                    )
                    == 1
                )
                assert (
                    await jobs.run_once(
                        db, "synthetic-actions-worker", kinds=[automation_actions.KIND]
                    )
                    is None
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
