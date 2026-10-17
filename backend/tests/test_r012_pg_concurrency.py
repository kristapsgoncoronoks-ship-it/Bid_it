"""Real HTTP cash/reversal races across independent PostgreSQL connections."""

import asyncio
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import issued
from app.core.database import get_session
from app.main import app
from app.models.audit import AuditEvent
from app.models.issued_invoice import IssuedInvoice
from app.models.job import Job
from app.models.payment import Payment
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from tests.test_issuing import INVOICE, ISSUER

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated PostgreSQL")


@pytest.mark.parametrize("same_key", [True, False])
async def test_parallel_payment_and_reversal_commit_one_effect(same_key, monkeypatch):
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with sessions() as session:
            yield session

    original_load = issued._load
    barrier = asyncio.Barrier(3)
    armed = False
    invoice_id = None

    async def synchronized_load(db, org_id, target, *, lock=False):
        if armed and lock and target == invoice_id:
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return await original_load(db, org_id, target, lock=lock)

    monkeypatch.setattr(issued, "_load", synchronized_load)
    app.dependency_overrides[get_session] = get_test_session
    try:
        async with sessions() as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ) is False
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic R012 concurrency",
                    "name": "Owner",
                    "email": f"r012-{uuid.uuid4().hex}@example.com",
                    "password": "synthetic-test-password",
                },
            )
            assert registration.status_code == 201, registration.text
            org_id = registration.json()["organization"]["id"]
            client.headers["Authorization"] = (
                "Bearer " + registration.json()["token"]["access_token"]
            )
            assert (await client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
            assert (
                await client.put("/api/v1/modules/issuing", json={"enabled": True})
            ).status_code == 200
            response = await client.post("/api/v1/issued", json=INVOICE)
            assert response.status_code == 201, response.text
            invoice = response.json()
            invoice_id = invoice["id"]
            async with sessions() as session:
                session.add(
                    WebhookEndpoint(
                        org_id=org_id,
                        url="https://synthetic.example.test/events",
                        secret="synthetic-test-secret",
                        events="issued.payment",
                    )
                )
                await session.commit()
            key = f"cash-{uuid.uuid4()}"
            body = {
                "currency": invoice["currency"],
                "amount": "20",
                "expected_outstanding": invoice["outstanding"],
            }
            armed = True
            requests = await asyncio.gather(
                *[
                    client.post(
                        f"/api/v1/issued/{invoice_id}/payments",
                        json=body,
                        headers={"Idempotency-Key": key if same_key else f"{key}:{i}"},
                    )
                    for i in range(3)
                ]
            )
            armed = False
            assert sorted(r.status_code for r in requests) == (
                [200, 200, 200] if same_key else [200, 409, 409]
            ), [r.text for r in requests]
            successes = [r.json() for r in requests if r.status_code == 200]
            assert len({r["payment"]["id"] for r in successes}) == 1
            payment_id = successes[0]["payment"]["id"]
            reversal_key = f"reverse-{uuid.uuid4()}"
            reversal_body = {
                "currency": invoice["currency"],
                "reason": "Wrong cash recorded",
                "expected_outstanding": successes[0]["invoice"]["outstanding"],
            }
            barrier = asyncio.Barrier(3)
            armed = True
            reversals = await asyncio.gather(
                *[
                    client.post(
                        f"/api/v1/issued/{invoice_id}/payments/{payment_id}/reverse",
                        json=reversal_body,
                        headers={
                            "Idempotency-Key": reversal_key if same_key else f"{reversal_key}:{i}"
                        },
                    )
                    for i in range(3)
                ]
            )
            armed = False
            assert sorted(r.status_code for r in reversals) == (
                [200, 200, 200] if same_key else [200, 409, 409]
            ), [r.text for r in reversals]
            assert len({r.json()["payment"]["id"] for r in reversals if r.status_code == 200}) == 1
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.sum(Payment.amount)).where(Payment.org_id == org_id)
                    )
                ) == 0
                row = await session.get(IssuedInvoice, invoice_id)
                assert row.amount_paid == 0
                for model, condition, count in (
                    (Payment, Payment.org_id == org_id, 2),
                    (WebhookDelivery, WebhookDelivery.org_id == org_id, 2),
                    (Job, (Job.org_id == org_id) & (Job.kind == "webhook.deliver"), 2),
                    (
                        AuditEvent,
                        (AuditEvent.org_id == org_id) & (AuditEvent.action == "issued.payment"),
                        1,
                    ),
                    (
                        AuditEvent,
                        (AuditEvent.org_id == org_id)
                        & (AuditEvent.action == "issued.payment.reversed"),
                        1,
                    ),
                ):
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(model).where(condition)
                        )
                        == count
                    )
                # A committed reversal always points at an existing, same-org
                # original. The full ledger still explains the aggregate cache.
                reversal = await session.scalar(
                    select(Payment).where(Payment.org_id == org_id, Payment.amount < 0)
                )
                assert reversal.reversal_of_id == payment_id
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()
