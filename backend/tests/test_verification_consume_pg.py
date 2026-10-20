"""Purpose-bound auth tokens have one transactional claimant on PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.tenant import reset_current_org, set_current_org
from app.models.email_token import PURPOSE_PASSWORD_RESET, PURPOSE_VERIFY_EMAIL, EmailToken
from app.models.user import User
from app.services import verification
from tests.test_capture_admission_pg import quota_workspace as quota_workspace

pytestmark = pytest.mark.skipif(
    not os.environ.get("RLS_TEST_DATABASE_URL"), reason="requires migrated non-superuser PostgreSQL"
)


async def _issue(workspace, purpose, *, expired=False, used=False):
    client, factory, org_id, _ = workspace
    identity = (await client.get("/api/v1/auth/me")).json()
    uid = identity["user"]["id"]
    async with factory() as db:
        assert (
            await db.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
        ) is False
        user = await db.get(User, uid)
        raw, row = await verification.issue_token(db, user, purpose, timedelta(hours=1))
        if expired:
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        if used:
            row.used_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
        return factory, raw, row.id, org_id, uid


async def _blocked_by(factory, waiter, holder):
    """Observe real database contention before releasing the first transaction."""

    async def observe():
        async with factory() as db:
            while True:
                blockers = await db.scalar(
                    text("SELECT pg_blocking_pids(:waiter)"), {"waiter": waiter}
                )
                if holder in blockers:
                    return
                await asyncio.sleep(0.01)

    await asyncio.wait_for(observe(), timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", [PURPOSE_VERIFY_EMAIL, PURPOSE_PASSWORD_RESET])
@pytest.mark.parametrize("first_commits", [True, False], ids=["commit", "rollback"])
async def test_same_auth_token_has_one_committed_claim_and_rollback_restores_it(
    quota_workspace, purpose, first_commits
):
    factory, raw, token_id, org_id, uid = await _issue(quota_workspace, purpose)
    second_pid = asyncio.Future()

    async def second_claim():
        async with factory() as db:
            second_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            row = await verification.consume(db, raw, purpose)
            result = None if row is None else (row.id, row.org_id, row.user_id, row.used_at)
            await db.commit()
            return result

    pending = None
    async with factory() as first:
        holder = await first.scalar(text("SELECT pg_backend_pid()"))
        row = await verification.consume(first, raw, purpose)
        assert row is not None and row.id == token_id and row.used_at is not None
        # The preceding implementation deferred its UPDATE until flush. Hold
        # that same real row lock in both versions before the competing claim.
        await first.flush()
        pending = asyncio.create_task(second_claim())
        try:
            waiter = await asyncio.wait_for(second_pid, timeout=10)
            assert holder != waiter
            await _blocked_by(factory, waiter, holder)
            assert not pending.done()
            if first_commits:
                await first.commit()
            else:
                await first.rollback()
            result = await asyncio.wait_for(pending, timeout=10)
        finally:
            await first.rollback()
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    if first_commits:
        assert result is None, "Both independent transactions claimed the same committed token"
    else:
        assert result is not None and result[:3] == (token_id, org_id, uid)
        assert result[3] is not None
    async with factory() as db:
        persisted = await db.get(EmailToken, token_id)
        assert persisted.used_at is not None
        assert persisted.token_hash == verification._hash(raw) and persisted.token_hash != raw
        assert await verification.consume(db, raw, purpose) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", [PURPOSE_VERIFY_EMAIL, PURPOSE_PASSWORD_RESET])
@pytest.mark.parametrize("refusal", ["wrong_purpose", "expired", "used", "unknown"])
async def test_refused_auth_token_preserves_its_recorded_state(quota_workspace, purpose, refusal):
    factory, raw, token_id, _, _ = await _issue(
        quota_workspace, purpose, expired=refusal == "expired", used=refusal == "used"
    )
    other_purpose = (
        PURPOSE_PASSWORD_RESET if purpose == PURPOSE_VERIFY_EMAIL else PURPOSE_VERIFY_EMAIL
    )
    async with factory() as db:
        row = await db.get(EmailToken, token_id)
        before = (row.token_hash, row.purpose, row.expires_at, row.used_at)
        value = raw + "synthetic-invalid" if refusal == "unknown" else raw
        assert (
            await verification.consume(
                db, value, other_purpose if refusal == "wrong_purpose" else purpose
            )
        ) is None
        await db.commit()
    async with factory() as db:
        row = await db.get(EmailToken, token_id)
        assert (row.token_hash, row.purpose, row.expires_at, row.used_at) == before
        if refusal in {"wrong_purpose", "unknown"}:
            assert await verification.consume(db, raw, purpose) is not None
            await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", [PURPOSE_VERIFY_EMAIL, PURPOSE_PASSWORD_RESET])
async def test_waiting_auth_claim_checks_expiry_after_first_claim_rolls_back(
    quota_workspace, monkeypatch, purpose
):
    factory, raw, token_id, _, _ = await _issue(quota_workspace, purpose)
    clock = [datetime.now(UTC)]

    class ControlledClock:
        @classmethod
        def now(cls, tz):
            return clock[0]

    async with factory() as db:
        row = await db.get(EmailToken, token_id)
        expires = clock[0] + timedelta(seconds=1)
        row.expires_at = expires
        await db.commit()
    monkeypatch.setattr(verification, "datetime", ControlledClock)
    second_pid = asyncio.Future()

    async def second_claim():
        async with factory() as db:
            second_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            row = await verification.consume(db, raw, purpose)
            claimed = row is not None
            await db.commit()
            return claimed

    pending = None
    async with factory() as first:
        holder = await first.scalar(text("SELECT pg_backend_pid()"))
        assert await verification.consume(first, raw, purpose) is not None
        await first.flush()
        pending = asyncio.create_task(second_claim())
        try:
            waiter = await asyncio.wait_for(second_pid, timeout=10)
            assert holder != waiter
            await _blocked_by(factory, waiter, holder)
            # Advance the application clock only after PostgreSQL proves the
            # second transaction is waiting. No real-time expiry sleep is used.
            clock[0] = expires + timedelta(seconds=1)
            await first.rollback()
            assert await asyncio.wait_for(pending, timeout=10) is False
        finally:
            await first.rollback()
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    async with factory() as db:
        row = await db.get(EmailToken, token_id)
        assert row.used_at is None and row.expires_at == expires


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", [PURPOSE_VERIFY_EMAIL, PURPOSE_PASSWORD_RESET])
async def test_auth_claim_preserves_existing_tenant_context_scope(quota_workspace, purpose):
    factory, raw, token_id, org_id, _ = await _issue(quota_workspace, purpose)
    wrong_scope = set_current_org(str(uuid4()))
    try:
        async with factory() as db:
            assert await verification.consume(db, raw, purpose) is None
            await db.commit()
    finally:
        reset_current_org(wrong_scope)
    async with factory() as db:
        row = await db.get(EmailToken, token_id)
        assert row.org_id == org_id and row.used_at is None
        assert await verification.consume(db, raw, purpose) is not None
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", [PURPOSE_VERIFY_EMAIL, PURPOSE_PASSWORD_RESET])
async def test_auth_claim_refreshes_preloaded_expiry_from_database(quota_workspace, purpose):
    factory, raw, token_id, _, _ = await _issue(quota_workspace, purpose)
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    async with factory() as first:
        cached = await first.get(EmailToken, token_id)
        original_expiry = cached.expires_at
        async with factory() as second:
            changed = await second.get(EmailToken, token_id)
            changed.expires_at = expired_at
            await second.commit()
        # A long-lived identity map still carries the earlier valid timestamp.
        assert cached.expires_at == original_expiry and cached.expires_at > expired_at
        assert await verification.consume(first, raw, purpose) is None
        assert cached.expires_at == expired_at
        await first.commit()
    async with factory() as db:
        persisted = await db.get(EmailToken, token_id)
        assert persisted.expires_at == expired_at and persisted.used_at is None
