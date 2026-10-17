"""R015 PostgreSQL command races, payload binding, byte truth and fresh access."""

import asyncio
import hashlib
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from app.models.audit import AuditEvent
from app.models.issued_invoice import IssuedInvoiceAttachment
from app.models.user import User
from app.services import filesec
from tests.test_project_profitability import _issuer_ready

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")


@pytest.mark.asyncio
async def test_attachment_concurrency_download_hash_and_scan_offboarding(monkeypatch):
    engine = create_async_engine(PG_URL, pool_size=5, max_overflow=0)
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
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic attachment PG",
                    "name": "Synthetic Owner",
                    "email": f"attachment-{uuid.uuid4().hex}@example.com",
                    "password": "Synthetic-Attachment-Postgres-Password-32",
                },
            )
            assert response.status_code == 201
            client.headers["Authorization"] = "Bearer " + response.json()["token"]["access_token"]
            await _issuer_ready(client)
            identity = (await client.get("/api/v1/auth/me")).json()
            org_id, user_id = identity["organization"]["id"], identity["user"]["id"]
            inv = await client.post(
                "/api/v1/issued",
                json={
                    "buyer_name": "Synthetic Buyer",
                    "lines": [
                        {
                            "description": "Synthetic work",
                            "quantity": "1",
                            "unit_price": "100",
                            "vat_rate": "0",
                        }
                    ],
                },
            )
            assert inv.status_code == 201
            iid = inv.json()["id"]
            path = f"/api/v1/issued/{iid}/attachments"
            data = b"Synthetic attachment bytes for exact download comparison"

            async def post(payload=data, key="synthetic-concurrent-command"):
                return await client.post(
                    path,
                    headers={"Idempotency-Key": key},
                    files={"file": ("synthetic.txt", payload, "text/plain")},
                )

            responses = await asyncio.gather(post(), post())
            assert sorted(r.status_code for r in responses) == [200, 201]
            assert responses[0].json()["id"] == responses[1].json()["id"]
            aid = responses[0].json()["id"]
            assert (await post(b"Different bytes")).status_code == 409
            downloaded = await client.get(path + f"/{aid}/download")
            assert downloaded.status_code == 200 and downloaded.content == data
            assert downloaded.headers["x-content-type-options"] == "nosniff"
            assert "attachment" in downloaded.headers["content-disposition"]
            async with factory() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(IssuedInvoiceAttachment)
                        .where(IssuedInvoiceAttachment.org_id == org_id)
                    )
                    == 1
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(AuditEvent)
                        .where(
                            AuditEvent.org_id == org_id, AuditEvent.action == "issued.attachment"
                        )
                    )
                    == 1
                )
                row = await db.get(IssuedInvoiceAttachment, aid)
                assert hashlib.sha256(downloaded.content).hexdigest() == row.sha256

            async def revoke(data):
                async with factory() as db:
                    await db.execute(update(User).where(User.id == user_id).values(is_active=False))
                    await db.commit()

            monkeypatch.setattr(filesec, "reject_active_content_async", revoke)
            denied = await post(key="synthetic-revoked-command")
            assert denied.status_code == 401
            async with factory() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(IssuedInvoiceAttachment)
                        .where(IssuedInvoiceAttachment.org_id == org_id)
                    )
                    == 1
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
