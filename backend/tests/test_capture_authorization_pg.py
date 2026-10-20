"""Capture must not use authorization cached before slow file work (real PG)."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.tenant import reset_current_org, set_current_org
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.plan_policy import PlanPolicy
from app.models.session import Session
from app.models.user import User, UserRole
from app.services import access, documents, filesec
from tests.test_capture_admission_pg import CSV, facts
from tests.test_capture_admission_pg import quota_workspace as quota_workspace

pytestmark = pytest.mark.skipif(
    not os.environ.get("RLS_TEST_DATABASE_URL"), reason="requires migrated non-superuser PostgreSQL"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "change"),
    [("scan", kind) for kind in ("user", "membership", "session", "organization")]
    + [("storage", kind) for kind in ("user", "membership", "session")],
)
async def test_capture_rejects_access_revoked_during_file_work(
    quota_workspace, monkeypatch, phase, change
):
    client, factory, org_id, _ = quota_workspace
    user_id = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
    original_scan, original_store = filesec.check_async, documents.store
    stored = 0

    async def revoke():
        model, condition, values = {
            "user": (User, User.id == user_id, {"is_active": False}),
            "membership": (Membership, Membership.user_id == user_id, {"status": "suspended"}),
            "session": (Session, Session.user_id == user_id, {"revoked_at": datetime.now(UTC)}),
            "organization": (Organization, Organization.id == org_id, {"status": "suspended"}),
        }[change]
        async with factory() as db:
            await db.execute(update(model).where(condition).values(**values))
            await db.commit()

    async def scan(*args, **kwargs):
        await original_scan(*args, **kwargs)
        if phase == "scan":
            await revoke()

    async def store(*args, **kwargs):
        nonlocal stored
        stored += 1
        result = await original_store(*args, **kwargs)
        if phase == "storage":
            await revoke()
        return result

    monkeypatch.setattr(filesec, "check_async", scan)
    monkeypatch.setattr(documents, "store", store)
    response = await client.post(
        "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
    )
    assert response.status_code == 401
    assert await facts(factory, org_id) == [0, 0, 0, 0]
    assert stored == (1 if phase == "storage" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["scan", "storage"])
async def test_capture_never_moves_to_a_session_changed_workspace(
    quota_workspace, monkeypatch, phase
):
    client, factory, org_id, _ = quota_workspace
    user_id = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
    async with factory() as db:
        other = Organization(name="Synthetic alternate capture workspace")
        db.add(other)
        await db.commit()
        other_id = other.id
    token = set_current_org(other_id)
    try:
        async with factory() as db:
            db.add(Membership(org_id=other_id, user_id=user_id, role=UserRole.admin))
            await db.commit()
    finally:
        reset_current_org(token)
    owner, name = (filesec, "check_async") if phase == "scan" else (documents, "store")
    original = getattr(owner, name)

    async def switch(*args, **kwargs):
        result = await original(*args, **kwargs)
        switched = await client.post(f"/api/v1/auth/switch-org/{other_id}")
        assert switched.status_code == 200
        return result

    monkeypatch.setattr(owner, name, switch)
    response = await client.post(
        "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
    )
    # PG RLS can hide a session moved out of the pinned tenant. An opaque
    # credential refusal is the contract; never widen scope to classify it.
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert await facts(factory, org_id) == [0, 0, 0, 0]
    assert await facts(factory, other_id) == [0, 0, 0, 0]


@pytest.mark.asyncio
async def test_batch_preserves_prior_admission_and_stops_on_offboarding(
    quota_workspace, monkeypatch
):
    client, factory, org_id, policy = quota_workspace
    user_id = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
    async with factory() as db:
        await db.execute(
            update(PlanPolicy).where(PlanPolicy.plan == policy).values(monthly_upload_limit=3)
        )
        await db.commit()
    original = filesec.check_async
    scanned = 0

    async def scan(*args, **kwargs):
        nonlocal scanned
        await original(*args, **kwargs)
        scanned += 1
        if scanned == 2:
            async with factory() as db:
                await db.execute(
                    update(Membership)
                    .where(Membership.user_id == user_id)
                    .values(status="suspended")
                )
                await db.commit()

    monkeypatch.setattr(filesec, "check_async", scan)
    response = await client.post(
        "/api/v1/invoices/upload/batch",
        files=[("files", (f"source-{i}.csv", CSV + b"\n" * i, "text/csv")) for i in range(3)],
    )
    assert response.status_code == 401
    assert scanned == 2
    assert await facts(factory, org_id) == [1, 1, 1, 1]
    async with factory() as db:
        assert await access.usage_count(db, org_id, "upload") == 1


@pytest.mark.asyncio
async def test_refresh_does_not_touch_session_or_commit_open_admission(
    quota_workspace, monkeypatch
):
    client, factory, org_id, _ = quota_workspace
    user_id = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
    original_store, original_meter = documents.store, access.record_usage
    old_seen = datetime.now(UTC) - timedelta(minutes=6)

    async def store(*args, **kwargs):
        result = await original_store(*args, **kwargs)
        async with factory() as db:
            await db.execute(
                update(Session).where(Session.user_id == user_id).values(last_seen_at=old_seen)
            )
            await db.commit()
        return result

    async def meter(*args, **kwargs):
        assert await facts(factory, org_id) == [0, 0, 0, 0]
        await original_meter(*args, **kwargs)

    monkeypatch.setattr(documents, "store", store)
    monkeypatch.setattr(access, "record_usage", meter)
    response = await client.post(
        "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
    )
    assert response.status_code == 202
    assert await facts(factory, org_id) == [1, 1, 1, 1]
    async with factory() as db:
        assert (
            await db.scalar(select(Session.last_seen_at).where(Session.user_id == user_id))
            == old_seen
        )


@pytest.mark.asyncio
async def test_identity_refresh_preserves_explicit_reupload_registry_metadata(quota_workspace):
    from app.models.document import Document

    client, factory, org_id, policy = quota_workspace
    async with factory() as db:
        await db.execute(
            update(PlanPolicy).where(PlanPolicy.plan == policy).values(monthly_upload_limit=2)
        )
        await db.commit()
    for name in ("first.csv", "renamed-original.csv"):
        response = await client.post(
            "/api/v1/invoices/upload?override=true", files={"file": (name, CSV, "text/csv")}
        )
        assert response.status_code == 202
    assert await facts(factory, org_id) == [1, 2, 2, 1]
    async with factory() as db:
        assert await db.scalar(select(Document.filename)) == "renamed-original.csv"
        assert await access.usage_count(db, org_id, "upload") == 2


@pytest.mark.asyncio
async def test_scan_offboarding_is_refused_before_duplicate_information(
    quota_workspace, monkeypatch
):
    client, factory, org_id, policy = quota_workspace
    user_id = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
    async with factory() as db:
        await db.execute(
            update(PlanPolicy).where(PlanPolicy.plan == policy).values(monthly_upload_limit=2)
        )
        await db.commit()
    first = await client.post(
        "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
    )
    assert first.status_code == 202
    original = filesec.check_async

    async def scan(*args, **kwargs):
        await original(*args, **kwargs)
        async with factory() as db:
            await db.execute(
                update(Membership).where(Membership.user_id == user_id).values(status="suspended")
            )
            await db.commit()

    monkeypatch.setattr(filesec, "check_async", scan)
    response = await client.post(
        "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
    )
    assert response.status_code == 401
    assert await facts(factory, org_id) == [1, 1, 1, 1]
