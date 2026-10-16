"""Real migrated PostgreSQL EUR projection and tenant proof (no provider I/O)."""

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from app.models.fx import EcbRate
from tests.test_project_profitability import _issuer_ready, _project

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")


@pytest.mark.asyncio
async def test_project_fx_with_postgres_money_and_tenant_isolation():
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    quote_date = date(2088, 1, 1)
    prior = None

    async def session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = session
    try:
        async with factory() as db:
            assert (
                await db.scalar(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
                is False
            )
            prior = await db.scalar(
                select(EcbRate.rate).where(
                    EcbRate.currency == "USD", EcbRate.rate_date == quote_date
                )
            )
            await db.execute(
                insert(EcbRate)
                .values(currency="USD", rate_date=quote_date, rate=Decimal("1.111111"))
                .on_conflict_do_update(
                    index_elements=["rate_date", "currency"], set_={"rate": Decimal("1.111111")}
                )
            )
            await db.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic FX PG",
                    "name": "Synthetic Owner",
                    "email": f"fx-{uuid.uuid4().hex}@example.com",
                    "password": "Synthetic-Postgres-Password-32",
                },
            )
            assert registration.status_code == 201
            client.headers["Authorization"] = (
                "Bearer " + registration.json()["token"]["access_token"]
            )
            await _issuer_ready(client)
            pid = await _project(client)
            for currency in ("EUR", "USD"):
                response = await client.post(
                    "/api/v1/issued",
                    json={
                        "project_id": pid,
                        "buyer_name": "Synthetic Buyer",
                        "currency": currency,
                        "issue_date": quote_date.isoformat(),
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
                assert response.status_code == 201
            result = (await client.get(f"/api/v1/masters/projects/{pid}/pnl")).json()
            assert result["revenue"] == "190.00" and result["complete"] is True
            rows = (await client.get("/api/v1/masters/projects-pnl-summary")).json()
            assert len(rows) == 1 and rows[0]["revenue"] == "190.00"
            # Another real account must not see the project or a source trace.
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic Other FX",
                    "name": "Synthetic Other",
                    "email": f"fx-other-{uuid.uuid4().hex}@example.com",
                    "password": "Synthetic-Other-Password-32",
                },
            )
            client.headers["Authorization"] = "Bearer " + response.json()["token"]["access_token"]
            assert (await client.get(f"/api/v1/masters/projects/{pid}/pnl")).status_code == 404
            assert (await client.get("/api/v1/masters/projects-pnl-summary")).json() == []
    finally:
        app.dependency_overrides.clear()
        async with factory() as db:
            if prior is None:
                await db.execute(
                    delete(EcbRate).where(
                        EcbRate.currency == "USD", EcbRate.rate_date == quote_date
                    )
                )
            else:
                await db.execute(
                    insert(EcbRate)
                    .values(currency="USD", rate_date=quote_date, rate=prior)
                    .on_conflict_do_update(
                        index_elements=["rate_date", "currency"], set_={"rate": prior}
                    )
                )
            await db.commit()
        await engine.dispose()
