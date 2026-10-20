"""Durable per-file admission replay/status across nonprivileged PG sessions."""

import asyncio
import json
import os
import uuid

import pytest
from sqlalchemy import func, select

from app.models.document import Document
from app.models.extraction_run import ExtractionRun
from app.models.job import Job
from app.models.plan_policy import PlanPolicy
from app.models.usage import UsageCounter
from app.services import access, filesec, jobs
from tests.test_capture_admission_pg import quota_workspace as quota_workspace

pytestmark = pytest.mark.skipif(
    not os.environ.get("RLS_TEST_DATABASE_URL"), reason="requires migrated non-superuser PostgreSQL"
)
CSV = b"description,quantity,unit_price,tax_rate\nSynthetic durable command,1,100,21\n"


def source(name="command.csv", content=CSV, mime="text/csv"):
    return {"file": (name, content, mime)}


async def counts(factory):
    async with factory() as db:
        return [
            await db.scalar(select(func.count()).select_from(m))
            for m in (Document, ExtractionRun, Job, UsageCounter)
        ]


async def allow_two(factory, policy):
    async with factory() as db:
        row = await db.scalar(select(PlanPolicy).where(PlanPolicy.plan == policy))
        row.monthly_upload_limit = 2
        await db.commit()


@pytest.mark.asyncio
async def test_replay_after_lost_response_at_full_quota_is_one_admission(quota_workspace):
    client, factory, _, _ = quota_workspace
    key = uuid.uuid4().hex
    first = await client.post(
        "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
    )
    assert first.status_code == 202
    # Treat the response as lost. Recovery is the authenticated committed lookup.
    found = await client.get(f"/api/v1/invoices/upload-commands/{key}")
    assert found.status_code == 200
    assert found.json()["extraction_run_id"] == first.json()["extraction_run_id"]
    second = await client.post(
        "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
    )
    assert second.status_code == 202, second.text
    assert first.json()["extraction_run_id"] == second.json()["extraction_run_id"]
    assert await counts(factory) == [1, 1, 1, 1]
    async with factory() as db:
        assert await db.scalar(select(UsageCounter.count)) == 1
        assert (await jobs.run_once(db, "synthetic-command-worker")).status == "succeeded"
    found = await client.get(f"/api/v1/invoices/upload-commands/{key}")
    assert found.json()["status"] == "parsed"


@pytest.mark.asyncio
async def test_simultaneous_same_command_replays_before_final_quota_check(
    quota_workspace, monkeypatch
):
    client, factory, _, _ = quota_workspace
    key = uuid.uuid4().hex
    both, scanned = asyncio.Event(), 0
    original = filesec.check_async

    async def barrier(*args, **kwargs):
        nonlocal scanned
        await original(*args, **kwargs)
        scanned += 1
        if scanned == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=10)

    monkeypatch.setattr(filesec, "check_async", barrier)
    responses = await asyncio.gather(
        *[
            client.post("/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key})
            for _ in range(2)
        ]
    )
    assert [r.status_code for r in responses] == [202, 202]
    assert responses[0].json()["extraction_run_id"] == responses[1].json()["extraction_run_id"]
    assert await counts(factory) == [1, 1, 1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["bytes", "filename", "mime", "override"])
async def test_committed_key_rejects_changed_inputs_even_at_quota(quota_workspace, changed):
    client, factory, _, _ = quota_workspace
    key = uuid.uuid4().hex
    first = await client.post(
        "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
    )
    assert first.status_code == 202
    files = source(
        name="changed.csv" if changed == "filename" else "command.csv",
        content=CSV + b"\n" if changed == "bytes" else CSV,
        mime="application/octet-stream" if changed == "mime" else "text/csv",
    )
    response = await client.post(
        "/api/v1/invoices/upload" + ("?override=true" if changed == "override" else ""),
        files=files,
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 409 and response.json()["code"] == "command_payload_conflict"
    assert await counts(factory) == [1, 1, 1, 1]


@pytest.mark.asyncio
async def test_command_rollback_allows_same_key_recovery(quota_workspace, monkeypatch):
    client, factory, _, _ = quota_workspace
    key = uuid.uuid4().hex
    original = access.record_usage

    async def fail_after_meter(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("Synthetic command metering rollback")

    monkeypatch.setattr(access, "record_usage", fail_after_meter)
    with pytest.raises(RuntimeError, match="Synthetic command metering"):
        await client.post(
            "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
        )
    assert await counts(factory) == [0, 0, 0, 0]
    assert (await client.get(f"/api/v1/invoices/upload-commands/{key}")).status_code == 404
    monkeypatch.setattr(access, "record_usage", original)
    assert (
        await client.post(
            "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
        )
    ).status_code == 202
    assert await counts(factory) == [1, 1, 1, 1]


@pytest.mark.asyncio
async def test_batch_lost_response_replays_committed_prefix_and_only_admits_missing_file(
    quota_workspace, monkeypatch
):
    client, factory, _, policy = quota_workspace
    await allow_two(factory, policy)
    keys = [uuid.uuid4().hex, uuid.uuid4().hex]
    files = [
        ("files", ("same.csv", CSV, "text/csv")),
        ("files", ("same.csv", CSV + b"\n", "text/csv")),
    ]
    original, calls = access.record_usage, 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        await original(*args, **kwargs)
        if calls == 2:
            raise RuntimeError("Synthetic second command rollback")

    monkeypatch.setattr(access, "record_usage", fail_second)
    with pytest.raises(RuntimeError, match="Synthetic second command"):
        await client.post(
            "/api/v1/invoices/upload/batch", files=files, data={"command_keys": json.dumps(keys)}
        )
    assert await counts(factory) == [1, 1, 1, 1]
    assert (await client.get(f"/api/v1/invoices/upload-commands/{keys[0]}")).status_code == 200
    assert (await client.get(f"/api/v1/invoices/upload-commands/{keys[1]}")).status_code == 404
    monkeypatch.setattr(access, "record_usage", original)
    response = await client.post(
        "/api/v1/invoices/upload/batch", files=files, data={"command_keys": json.dumps(keys)}
    )
    assert response.status_code == 202 and response.json()["accepted"] == 2
    assert len({r["extraction_run_id"] for r in response.json()["outcomes"]}) == 2
    assert await counts(factory) == [2, 2, 2, 1]
    async with factory() as db:
        assert await db.scalar(select(UsageCounter.count)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("keys", ["not-json", "[]", '["valid-key", "valid-key"]', '["bad/key"]'])
async def test_invalid_batch_keys_refuse_before_any_admission(quota_workspace, keys):
    from app.models.capture_command import CaptureCommand

    client, factory, _, _ = quota_workspace
    response = await client.post(
        "/api/v1/invoices/upload/batch",
        files=[("files", ("source.csv", CSV, "text/csv"))],
        data={"command_keys": keys},
    )
    assert response.status_code == 422
    assert await counts(factory) == [0, 0, 0, 0]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(CaptureCommand)) == 0


@pytest.mark.asyncio
async def test_purged_capture_leaves_command_receipt_and_cannot_replay_admission(quota_workspace):
    from app.models.capture_command import CaptureCommand

    client, factory, _, _ = quota_workspace
    # A valid key overlapping an older invoice subroute must still reach lookup.
    key = "extraction"
    first = await client.post(
        "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
    )
    assert first.status_code == 202
    async with factory() as db:
        run = await db.get(ExtractionRun, first.json()["extraction_run_id"])
        await db.delete(run)
        await db.commit()
    lookup = await client.get(f"/api/v1/invoices/upload-commands/{key}")
    assert lookup.status_code == 200 and lookup.json() == {
        "extraction_run_id": None,
        "status": "retired",
    }
    assert lookup.headers["cache-control"] == "private, no-store"
    again = await client.post(
        "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
    )
    assert again.status_code == 409 and again.json()["code"] == "capture_command_retired"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(CaptureCommand)) == 1
        assert await db.scalar(select(func.count()).select_from(ExtractionRun)) == 0
        assert await db.scalar(select(UsageCounter.count)) == 1


@pytest.mark.asyncio
async def test_command_status_and_key_namespace_are_workspace_scoped(quota_workspace):
    client, factory, _, _ = quota_workspace
    key = uuid.uuid4().hex
    first = await client.post(
        "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
    )
    assert first.status_code == 202
    original_token = client.headers["Authorization"]
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Synthetic other command workspace",
            "name": "Synthetic Other Owner",
            "email": f"command-other-{uuid.uuid4().hex}@example.com",
            "password": "Synthetic-Other-Command-Password-32-Only",
        },
    )
    assert registered.status_code == 201
    client.headers["Authorization"] = "Bearer " + registered.json()["token"]["access_token"]
    assert (await client.get(f"/api/v1/invoices/upload-commands/{key}")).status_code == 404
    other = await client.post(
        "/api/v1/invoices/upload",
        files=source(content=CSV + b"\n"),
        headers={"Idempotency-Key": key},
    )
    assert other.status_code == 202
    assert other.json()["extraction_run_id"] != first.json()["extraction_run_id"]
    client.headers["Authorization"] = original_token
    assert (await client.get(f"/api/v1/invoices/upload-commands/{key}")).json()[
        "extraction_run_id"
    ] == first.json()["extraction_run_id"]


@pytest.mark.asyncio
async def test_access_revoked_during_command_lookup_exposes_no_result(quota_workspace, monkeypatch):
    from sqlalchemy import delete

    from app.models.membership import Membership
    from app.services import capture_commands

    client, factory, _, _ = quota_workspace
    key = uuid.uuid4().hex
    first = await client.post(
        "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
    )
    assert first.status_code == 202
    original, revoked = capture_commands.lookup, False

    async def revoke_after_lookup(*args, **kwargs):
        nonlocal revoked
        result = await original(*args, **kwargs)
        if not revoked:
            revoked = True
            async with factory() as other:
                await other.execute(delete(Membership))
                await other.commit()
        return result

    monkeypatch.setattr(capture_commands, "lookup", revoke_after_lookup)
    response = await client.get(f"/api/v1/invoices/upload-commands/{key}")
    assert response.status_code == 401
    assert first.json()["extraction_run_id"] not in response.text


@pytest.mark.asyncio
async def test_command_lookup_cannot_observe_uncommitted_admission(quota_workspace, monkeypatch):
    from app.models.capture_command import CaptureCommand

    client, factory, _, _ = quota_workspace
    key = uuid.uuid4().hex
    reached, release = asyncio.Event(), asyncio.Event()
    original = access.record_usage

    async def held_meter(*args, **kwargs):
        await original(*args, **kwargs)
        reached.set()
        await asyncio.wait_for(release.wait(), timeout=10)

    monkeypatch.setattr(access, "record_usage", held_meter)
    pending = asyncio.create_task(
        client.post("/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key})
    )
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        assert (await client.get(f"/api/v1/invoices/upload-commands/{key}")).status_code == 404
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(CaptureCommand)) == 0
            assert (
                await jobs.claim(db, "synthetic-command-precommit", kinds=("upload.extract",))
                is None
            )
    finally:
        release.set()
        response = await pending
    assert response.status_code == 202
    assert (await client.get(f"/api/v1/invoices/upload-commands/{key}")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["tenant", "duplicate"])
async def test_command_run_reference_enforces_tenant_and_one_admission_at_db(
    quota_workspace, violation
):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.core.tenant import reset_current_org, set_current_org
    from app.models.capture_command import CaptureCommand
    from app.models.organization import Organization

    client, factory, org_id, _ = quota_workspace
    key = uuid.uuid4().hex
    first = await client.post(
        "/api/v1/invoices/upload", files=source(), headers={"Idempotency-Key": key}
    )
    assert first.status_code == 202
    if violation == "tenant":
        token = set_current_org(None)
        try:
            async with factory() as db:
                other = Organization(name="Synthetic foreign command FK")
                db.add(other)
                await db.flush()
                command = CaptureCommand(org_id=other.id, command_key=key, payload_hash="a" * 64)
                db.add(command)
                await db.commit()
                command_id = command.id
        finally:
            reset_current_org(token)
    else:
        async with factory() as db:
            command_id = await db.scalar(select(CaptureCommand.id))
    async with factory() as db:
        # Raw SQL exercises FORCE RLS, independently of ORM loader criteria.
        assert await db.scalar(text("SELECT count(*) FROM capture_commands")) == 1
        db.add(
            ExtractionRun(
                org_id=org_id, method="pending", status="queued", admission_command_id=command_id
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()
