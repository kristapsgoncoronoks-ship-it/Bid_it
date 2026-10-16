"""Real PostgreSQL, real PDF bytes and independently committed offboarding."""

import hashlib
import io
import json
import os
import uuid
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import issued as route
from app.core.database import get_session
from app.main import app
from app.models.user import User
from tests.test_project_profitability import _issuer_ready

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")


@pytest.mark.asyncio
async def test_zip_real_pdf_tenant_boundary_and_separate_session_revocation(monkeypatch):
    engine = create_async_engine(PG_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

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

            async def register():
                r = await client.post(
                    "/api/v1/auth/register",
                    json={
                        "organization_name": "Synthetic ZIP PG",
                        "name": "Synthetic Owner",
                        "email": f"zip-{uuid.uuid4().hex}@example.com",
                        "password": "Synthetic-Zip-Postgres-Password-32",
                    },
                )
                assert r.status_code == 201
                client.headers["Authorization"] = "Bearer " + r.json()["token"]["access_token"]
                return client.headers["Authorization"]

            owner_token = await register()
            await _issuer_ready(client)
            issued = await client.post(
                "/api/v1/issued",
                json={
                    "buyer_name": "Synthetic ZIP Buyer",
                    "issue_date": "2087-01-01",
                    "currency": "EUR",
                    "lines": [
                        {
                            "description": "Synthetic work",
                            "quantity": "1",
                            "unit_price": "100.00",
                            "vat_rate": "0",
                        }
                    ],
                },
            )
            assert issued.status_code == 201
            invoice_id = issued.json()["id"]
            owner_id = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
            response = await client.get("/api/v1/issued/export.zip?from=2087-01-01&to=2087-01-01")
            assert response.status_code == 200
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["complete"] is True and manifest["count"] == 1
                item = manifest["documents"][0]
                assert item["id"] == invoice_id
                pdf = archive.read(item["filename"])
                assert pdf.startswith(b"%PDF") and len(pdf) > 1000
                assert hashlib.sha256(pdf).hexdigest() == item["pdf_sha256"]
            await register()
            await _issuer_ready(client)
            assert (await client.get("/api/v1/issued/export.zip")).status_code == 404
            client.headers["Authorization"] = owner_token
            real_render = route._render_pdf

            async def render_then_offboard(*args, **kwargs):
                pdf = await real_render(*args, **kwargs)
                async with factory() as other:
                    await other.execute(
                        update(User).where(User.id == owner_id).values(is_active=False)
                    )
                    await other.commit()
                return pdf

            monkeypatch.setattr(route, "_render_pdf", render_then_offboard)
            denied = await client.get("/api/v1/issued/export.zip")
            assert denied.status_code == 401 and not denied.content.startswith(b"PK")
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
