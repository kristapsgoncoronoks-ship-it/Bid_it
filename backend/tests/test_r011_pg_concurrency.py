"""Real pooled PostgreSQL HTTP command races; run in the existing postgres CI job."""

import asyncio
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_session
from app.main import app
from app.models.audit import AuditEvent
from app.models.email_message import EmailMessage
from app.models.issued_invoice import IssuedInvoice
from app.models.issuer import IssuerProfile
from app.models.job import Job
from app.services import invoice_delivery, issued_commands, jobs, mailer
from tests.test_issuing import INVOICE, ISSUER

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated PostgreSQL")


@pytest.mark.parametrize("draft", [False, True])
async def test_concurrent_create_and_finalize_commit_one_command(draft, monkeypatch):
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with sessions() as session:
            yield session

    barrier = asyncio.Barrier(3)
    original_lookup = issued_commands.lookup

    async def synchronized_lookup(*args, **kwargs):
        row = await original_lookup(*args, **kwargs)
        if row is None and kwargs.get("expected") is not None:
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return row

    monkeypatch.setattr(issued_commands, "lookup", synchronized_lookup)
    app.dependency_overrides[get_session] = get_test_session
    try:
        async with sessions() as session:
            assert (
                await session.scalar(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                )
            ) is False
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic R011 race",
                    "name": "Owner",
                    "email": f"r011-{uuid.uuid4().hex}@example.com",
                    "password": "synthetic-test-password",
                },
            )
            assert response.status_code == 201, response.text
            org_id = response.json()["organization"]["id"]
            client.headers["Authorization"] = "Bearer " + response.json()["token"]["access_token"]
            assert (await client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
            assert (
                await client.put("/api/v1/modules/issuing", json={"enabled": True})
            ).status_code == 200
            payload = {**INVOICE, "draft": draft}
            headers = {"Idempotency-Key": f"create-{uuid.uuid4()}"}
            responses = await asyncio.gather(
                *[client.post("/api/v1/issued", json=payload, headers=headers) for _ in range(3)]
            )
            assert [r.status_code for r in responses] == [201, 201, 201], [
                r.text for r in responses
            ]
            assert len({r.json()["id"] for r in responses}) == 1
            invoice_id = responses[0].json()["id"]
            if draft:
                issue_headers = {"Idempotency-Key": f"issue-{uuid.uuid4()}"}
                issued = await asyncio.gather(
                    *[
                        client.post(
                            f"/api/v1/issued/{invoice_id}/issue", json={}, headers=issue_headers
                        )
                        for _ in range(3)
                    ]
                )
                assert [r.status_code for r in issued] == [200, 200, 200], [r.text for r in issued]
                assert len({r.json()["number"] for r in issued}) == 1
            calls = []
            monkeypatch.setattr(settings, "smtp_host", "synthetic.test")
            monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
            delivery_headers = {"Idempotency-Key": f"delivery-{uuid.uuid4()}"}
            deliveries = await asyncio.gather(
                *[
                    client.post(
                        f"/api/v1/issued/{invoice_id}/send",
                        json={"to_email": "buyer@example.com"},
                        headers=delivery_headers,
                    )
                    for _ in range(3)
                ]
            )
            assert [r.status_code for r in deliveries] == [200, 200, 200], [
                r.text for r in deliveries
            ]
            assert len({r.json()["message"]["id"] for r in deliveries}) == 1
            assert calls == []
            async with sessions() as delivery_session:
                assert (
                    await delivery_session.scalar(
                        select(func.count())
                        .select_from(Job)
                        .where(Job.org_id == org_id, Job.kind == invoice_delivery.KIND)
                    )
                    == 1
                )
                # An independent connection sees committed intent before external I/O.
                message = await delivery_session.scalar(
                    select(EmailMessage).where(
                        EmailMessage.org_id == org_id, EmailMessage.kind == "invoice"
                    )
                )
                assert message.status == "queued"
                await jobs.run_once(
                    delivery_session, "r011-pg-worker", kinds=(invoice_delivery.KIND,)
                )
                await delivery_session.refresh(message)
                assert message.status == "sent"
            assert len(calls) == 1
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(IssuedInvoice)
                        .where(IssuedInvoice.org_id == org_id)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditEvent)
                        .where(AuditEvent.org_id == org_id, AuditEvent.action == "issued.create")
                    )
                    == 1
                )
                profile = await session.scalar(
                    select(IssuerProfile).where(IssuerProfile.org_id == org_id)
                )
                assert profile.next_number == 2
                if draft:
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(AuditEvent)
                            .where(AuditEvent.org_id == org_id, AuditEvent.action == "issued.issue")
                        )
                        == 1
                    )
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()
