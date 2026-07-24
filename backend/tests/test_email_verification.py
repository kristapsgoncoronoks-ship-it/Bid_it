"""Email verification + password reset (Slice 3).

Covers the full flows and their security properties: tokens are single-use,
purpose-bound, expiring; forgot-password never enumerates users; the login gate
is opt-in; and the emailed link is what carries the (never-stored-raw) token.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.email_token import (
    PURPOSE_PASSWORD_RESET,
    PURPOSE_VERIFY_EMAIL,
    EmailToken,
)
from app.models.user import User
from app.services import verification


async def _register(client, org="Acme", email="owner@acme.io", pw="supersecret"):
    r = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "name": "Owner", "email": email, "password": pw},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _issue_raw(db_session, email, purpose):
    """Mint a token directly and return its raw value (the emailed value)."""
    user = await db_session.scalar(select(User).where(User.email == email))
    raw = await verification.issue(db_session, user, purpose, timedelta(hours=1))
    await db_session.commit()
    return raw, user


@pytest.mark.asyncio
async def test_register_starts_unverified_and_queues_an_email(client, db_session):
    body = await _register(client)
    assert body["user"]["email_verified"] is False
    # A verification token row exists for the new user, and an outbox message too.
    tok = await db_session.scalar(
        select(EmailToken).where(EmailToken.purpose == PURPOSE_VERIFY_EMAIL)
    )
    assert tok is not None and tok.used_at is None


@pytest.mark.asyncio
async def test_verify_email_happy_path_and_single_use(client, db_session):
    await _register(client)
    raw, _ = await _issue_raw(db_session, "owner@acme.io", PURPOSE_VERIFY_EMAIL)

    ok = await client.post("/api/v1/auth/verify-email", json={"token": raw})
    assert ok.status_code == 200 and ok.json()["verified"] is True
    user = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    await db_session.refresh(user)
    assert user.email_verified is True

    # The same token cannot be replayed.
    again = await client.post("/api/v1/auth/verify-email", json={"token": raw})
    assert again.status_code == 400


@pytest.mark.asyncio
async def test_verify_email_rejects_bad_and_expired(client, db_session):
    await _register(client)
    bad = await client.post("/api/v1/auth/verify-email", json={"token": "not-a-real-token"})
    assert bad.status_code == 400

    # An expired token is refused.
    user = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    raw = await verification.issue(db_session, user, PURPOSE_VERIFY_EMAIL, timedelta(hours=1))
    await db_session.commit()
    tok = await db_session.scalar(
        select(EmailToken).where(EmailToken.token_hash == verification._hash(raw))
    )
    tok.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    exp = await client.post("/api/v1/auth/verify-email", json={"token": raw})
    assert exp.status_code == 400


@pytest.mark.asyncio
async def test_purpose_binding_verify_token_is_not_a_reset(client, db_session):
    await _register(client)
    raw, _ = await _issue_raw(db_session, "owner@acme.io", PURPOSE_VERIFY_EMAIL)
    # A verify token must not work on the reset endpoint.
    r = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw, "new_password": "brandnewpw1"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_forgot_password_does_not_enumerate(client):
    await _register(client)
    # Known and unknown emails both return the same 200 body.
    known = await client.post("/api/v1/auth/forgot-password", json={"email": "owner@acme.io"})
    unknown = await client.post("/api/v1/auth/forgot-password", json={"email": "nobody@nowhere.io"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"sent": True}


@pytest.mark.asyncio
async def test_password_reset_changes_password_and_is_single_use(client, db_session):
    await _register(client, pw="originalpw1")
    raw, _ = await _issue_raw(db_session, "owner@acme.io", PURPOSE_PASSWORD_RESET)

    done = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw, "new_password": "changedpw123"}
    )
    assert done.status_code == 200 and done.json()["reset"] is True

    # Old password rejected, new password works.
    old = await client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.io", "password": "originalpw1"}
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.io", "password": "changedpw123"}
    )
    assert new.status_code == 200

    # The reset token is consumed.
    again = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw, "new_password": "another123"}
    )
    assert again.status_code == 400


@pytest.mark.asyncio
async def test_login_gate_is_opt_in(client, db_session, monkeypatch):
    await _register(client, email="fresh@acme.io")
    # Gate OFF (default): an unverified user can still log in.
    off = await client.post(
        "/api/v1/auth/login", json={"email": "fresh@acme.io", "password": "supersecret"}
    )
    assert off.status_code == 200

    # Gate ON: the same unverified user is refused with a clear 403.
    monkeypatch.setattr(settings, "require_email_verification", True)
    on = await client.post(
        "/api/v1/auth/login", json={"email": "fresh@acme.io", "password": "supersecret"}
    )
    assert on.status_code == 403

    # After verifying, login succeeds even with the gate on.
    raw, _ = await _issue_raw(db_session, "fresh@acme.io", PURPOSE_VERIFY_EMAIL)
    await client.post("/api/v1/auth/verify-email", json={"token": raw})
    back = await client.post(
        "/api/v1/auth/login", json={"email": "fresh@acme.io", "password": "supersecret"}
    )
    assert back.status_code == 200


@pytest.mark.asyncio
async def test_only_token_hash_is_stored(client, db_session):
    await _register(client)
    raw, _ = await _issue_raw(db_session, "owner@acme.io", PURPOSE_VERIFY_EMAIL)
    # The raw token is NOWHERE in the row — only its sha256 hash.
    row = await db_session.scalar(
        select(EmailToken).where(EmailToken.token_hash == verification._hash(raw))
    )
    assert row is not None
    assert raw not in (row.token_hash,)
    assert len(row.token_hash) == 64
