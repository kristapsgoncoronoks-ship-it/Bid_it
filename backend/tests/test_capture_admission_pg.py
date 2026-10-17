"""Workers must see either all capture admission facts or none (real PostgreSQL)."""

import asyncio
import json
import os
import uuid

import pytest
import pytest_asyncio
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


@pytest_asyncio.fixture
async def quota_workspace():
    """A private policy avoids changing the shared trial policy in PG CI."""
    from app.models.organization import Organization
    from app.models.plan_policy import PlanPolicy

    engine = create_async_engine(PG_URL, pool_size=6, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def session():
        async with factory() as db:
            yield db

    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registered = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic quota concurrency",
                    "name": "Synthetic Owner",
                    "email": f"quota-{uuid.uuid4().hex}@example.com",
                    "password": "Synthetic-Capture-PG-Password-32-Only",
                },
            )
            assert registered.status_code == 201
            client.headers["Authorization"] = "Bearer " + registered.json()["token"]["access_token"]
            org_id = (await client.get("/api/v1/auth/me")).json()["organization"]["id"]
            policy = "q" + uuid.uuid4().hex[:15]
            token = set_current_org(org_id)
            try:
                async with factory() as db:
                    assert not await db.scalar(
                        text(
                            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                        )
                    )
                    org = await db.get(Organization, org_id)
                    org.plan = policy
                    db.add(PlanPolicy(plan=policy, monthly_upload_limit=1, monthly_invoice_limit=0))
                    await db.commit()
                yield client, factory, org_id, policy
            finally:
                reset_current_org(token)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("abort_first", [False, True])
async def test_single_and_batch_compete_for_last_slot(quota_workspace, monkeypatch, abort_first):
    from app.services import filesec

    client, factory, org_id, _ = quota_workspace
    original_scan, original_meter = filesec.check_async, access.record_usage
    both_scanned = asyncio.Event()
    scanned = 0
    metered = 0

    async def scan_barrier(*args, **kwargs):
        nonlocal scanned
        await original_scan(*args, **kwargs)
        scanned += 1
        if scanned == 2:
            both_scanned.set()
        await asyncio.wait_for(both_scanned.wait(), timeout=15)

    async def fail_first_meter(*args, **kwargs):
        nonlocal metered
        metered += 1
        this_call = metered
        await original_meter(*args, **kwargs)
        if abort_first and this_call == 1:
            raise MeterFault("Synthetic winning admission rolled back")

    monkeypatch.setattr(filesec, "check_async", scan_barrier)
    monkeypatch.setattr(access, "record_usage", fail_first_meter)
    results = await asyncio.wait_for(
        asyncio.gather(
            client.post(
                "/api/v1/invoices/upload?override=true",
                files={"file": ("single.csv", CSV, "text/csv")},
            ),
            client.post(
                "/api/v1/invoices/upload/batch?override=true",
                files=[("files", ("batch.csv", CSV + b"\n", "text/csv"))],
            ),
            return_exceptions=True,
        ),
        timeout=30,
    )
    accepted = refused = failed = 0
    for result in results:
        if isinstance(result, MeterFault):
            failed += 1
        else:
            assert not isinstance(result, BaseException), type(result).__name__
            if result.status_code == 402:
                refused += 1
            else:
                assert result.status_code == 202
                body = result.json()
                if "outcomes" in body:
                    accepted += body["accepted"]
                    refused += body["rejected"]
                    if body["rejected"]:
                        assert body["outcomes"][0]["code"] == "upload_quota_reached"
                else:
                    accepted += 1
    assert (accepted, refused, failed) == ((1, 0, 1) if abort_first else (1, 1, 0))
    assert await facts(factory, org_id) == [1, 1, 1, 1]
    async with factory() as db:
        assert await access.usage_count(db, org_id, "upload") == 1
        processed = await jobs.run_once(db, "synthetic-quota-worker", kinds=("upload.extract",))
        assert processed.status == "succeeded"


@pytest.mark.asyncio
async def test_missing_policy_does_not_commit_or_lock_other_workspaces(quota_workspace):
    from sqlalchemy import delete
    from sqlalchemy.exc import DBAPIError

    from app.models.organization import Organization
    from app.models.plan_policy import PlanPolicy

    _, factory, org_id, policy = quota_workspace
    async with factory() as db:
        await db.execute(delete(PlanPolicy).where(PlanPolicy.plan == policy))
        other = Organization(name="Synthetic independent quota workspace", plan=policy)
        db.add(other)
        await db.commit()
        other_id = other.id
    async with factory() as admission:
        await access.lock_upload_quota(admission, org_id)
        await access.record_usage(admission, org_id, "upload", commit=False)
        # A missing policy must not trigger _get_or_seed's implicit commit.
        assert await facts(factory, org_id) == [0, 0, 0, 0]
        async with factory() as observer:
            with pytest.raises(DBAPIError) as locked:
                await observer.execute(
                    select(Organization.id)
                    .where(Organization.id == org_id)
                    .with_for_update(nowait=True)
                )
            assert locked.value.orig.sqlstate == "55P03"
            await observer.rollback()
        token = set_current_org(other_id)
        try:
            async with factory() as independent:
                await asyncio.wait_for(access.lock_upload_quota(independent, other_id), timeout=5)
                await independent.rollback()
        finally:
            reset_current_org(token)
        await admission.rollback()
    assert await facts(factory, org_id) == [0, 0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["plan", "policy"])
async def test_quota_rechecks_committed_plan_and_policy_after_scan(
    quota_workspace, monkeypatch, change
):
    from app.models.organization import Organization
    from app.models.plan_policy import PlanPolicy
    from app.services import filesec

    client, factory, org_id, policy = quota_workspace
    async with factory() as db:
        limits = await db.scalar(select(PlanPolicy).where(PlanPolicy.plan == policy))
        limits.monthly_upload_limit = 2
        await access.record_usage(db, org_id, "upload", commit=False)
        await db.commit()
    original_scan = filesec.check_async

    async def change_during_scan(*args, **kwargs):
        await original_scan(*args, **kwargs)
        async with factory() as db:
            if change == "policy":
                limits = await db.scalar(select(PlanPolicy).where(PlanPolicy.plan == policy))
                limits.monthly_upload_limit = 1
            else:
                replacement = "q" + uuid.uuid4().hex[:15]
                db.add(
                    PlanPolicy(plan=replacement, monthly_upload_limit=1, monthly_invoice_limit=0)
                )
                org = await db.get(Organization, org_id)
                org.plan = replacement
            await db.commit()

    monkeypatch.setattr(filesec, "check_async", change_during_scan)
    result = await client.post(
        "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
    )
    assert result.status_code == 402
    assert await facts(factory, org_id) == [0, 0, 0, 1]
    async with factory() as db:
        assert await access.usage_count(db, org_id, "upload") == 1
