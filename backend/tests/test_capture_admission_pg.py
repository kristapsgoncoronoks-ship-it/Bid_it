"""Workers must see either all capture admission facts or none (real PostgreSQL)."""

import asyncio
import json
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.core.tenant import reset_current_org, set_current_org
from app.main import app
from app.models.document import Document
from app.models.extraction_run import ExtractionRun
from app.models.job import Job
from app.models.usage import UsageCounter
from app.services import access, jobs

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated non-superuser PostgreSQL")
CSV = b"description,quantity,unit_price,tax_rate\nSynthetic PG atomic source,1,100,21\n"


class MeterFault(RuntimeError):
    pass


async def facts(factory, org_id):
    token = set_current_org(org_id)
    try:
        async with factory() as db:
            return [
                await db.scalar(select(func.count()).select_from(model))
                for model in (Document, ExtractionRun, Job, UsageCounter)
            ]
    finally:
        reset_current_org(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("abort", [False, True])
async def test_capture_visibility_and_meter_rollback_across_connections(monkeypatch, abort):
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def session():
        async with factory() as db:
            yield db

    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = session
    paused, release = asyncio.Event(), asyncio.Event()
    original_meter = access.record_usage

    async def held_meter(*args, **kwargs):
        paused.set()
        await asyncio.wait_for(release.wait(), timeout=10)
        await original_meter(*args, **kwargs)
        if abort:
            raise MeterFault("Synthetic failure after metering statement")

    try:
        async with factory() as db:
            assert (
                await db.scalar(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
                is False
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registered = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic capture atomicity",
                    "name": "Synthetic Owner",
                    "email": f"capture-{uuid.uuid4().hex}@example.com",
                    "password": "Synthetic-Capture-PG-Password-32-Only",
                },
            )
            assert registered.status_code == 201
            client.headers["Authorization"] = "Bearer " + registered.json()["token"]["access_token"]
            identity = (await client.get("/api/v1/auth/me")).json()
            org_id = identity["organization"]["id"]
            monkeypatch.setattr(access, "record_usage", held_meter)

            async def post():
                return await client.post(
                    "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
                )

            pending = asyncio.create_task(post())
            try:
                await asyncio.wait_for(paused.wait(), timeout=10)
                before = await facts(factory, org_id)
                token = set_current_org(org_id)
                try:
                    async with factory() as db:
                        claimed = await jobs.claim(
                            db, "synthetic-precommit-observer", kinds=("upload.extract",)
                        )
                finally:
                    reset_current_org(token)
            finally:
                release.set()
                if abort:
                    with pytest.raises(MeterFault):
                        await pending
                else:
                    accepted = await pending
                    assert accepted.status_code == 202
            assert before == [0, 0, 0, 0], "Admission facts became visible before the final commit"
            assert claimed is None, "A worker could claim a capture before admission finished"
            monkeypatch.setattr(access, "record_usage", original_meter)
            if abort:
                assert await facts(factory, org_id) == [0, 0, 0, 0]
                accepted = await post()
                assert accepted.status_code == 202
            assert await facts(factory, org_id) == [1, 1, 1, 1]
            token = set_current_org(org_id)
            try:
                async with factory() as db:
                    run = await db.scalar(select(ExtractionRun))
                    job = await db.scalar(select(Job))
                    assert json.loads(job.payload_json)["run_id"] == run.id
                    assert (await db.scalar(select(UsageCounter.count))) == 1
                    processed = await jobs.run_once(
                        db, "synthetic-after-commit", kinds=("upload.extract",)
                    )
                    assert processed.status == "succeeded"
                    await db.refresh(run)
                    assert run.status == "parsed"
                    assert (await db.scalar(select(UsageCounter.count))) == 1
            finally:
                reset_current_org(token)
    finally:
        release.set()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)
        await engine.dispose()
