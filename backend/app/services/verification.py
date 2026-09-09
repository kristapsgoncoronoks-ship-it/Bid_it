"""Email verification + password reset tokens (Slice 3).

Issues single-use, purpose-bound, hashed tokens and consumes them safely. Emails
go through the existing `mailer` (recorded to the outbox; delivered when SMTP is
configured; never raises). The raw token lives only in the emailed link.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.email_token import (
    PURPOSE_PASSWORD_RESET,
    PURPOSE_VERIFY_EMAIL,
    EmailToken,
)
from app.services import mailer

_VERIFY_TTL = timedelta(hours=24)
_RESET_TTL = timedelta(hours=1)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue(
    db: AsyncSession, user, purpose: str, ttl: timedelta, *, org_id: str | None = None
) -> str:
    """Create a token row (storing only its hash) and return the RAW token.
    Does not commit — the caller commits with its own operation.

    `org_id` names the workspace the token belongs to. It defaults to
    `user.org_id`, which is the user's ACTIVE-org pointer and therefore only
    correct when the token is issued during a request in that org. A token
    issued on the worker, minutes after the request that asked for it, must
    pass the org explicitly: the user may have switched workspaces in between,
    and on Postgres the `WITH CHECK` leg of `email_tokens`' RLS policy would
    refuse the INSERT outright (PROD-009 review, S3)."""
    raw, _token = await issue_token(db, user, purpose, ttl, org_id=org_id)
    return raw


async def issue_token(
    db: AsyncSession, user, purpose: str, ttl: timedelta, *, org_id: str | None = None
) -> tuple[str, EmailToken]:
    """`issue`, plus the row itself — for a caller that must record WHICH token
    it handed out, so that redeeming it can resolve exactly the artefact it was
    issued for rather than "the newest one that happens to be ready"."""
    raw = secrets.token_urlsafe(32)
    token = EmailToken(
        org_id=org_id or user.org_id,
        user_id=user.id,
        purpose=purpose,
        token_hash=_hash(raw),
        expires_at=datetime.now(UTC) + ttl,
    )
    db.add(token)
    await db.flush()
    return raw, token


async def consume(db: AsyncSession, raw: str, purpose: str) -> EmailToken | None:
    """Validate and single-use-consume a token for `purpose`. Returns the row on
    success (marked used), or None if unknown / wrong purpose / already used /
    expired. Does not commit."""
    row = await db.scalar(
        select(EmailToken).where(
            EmailToken.token_hash == _hash(raw),
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
    )
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:  # SQLite returns naive datetimes
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        return None
    row.used_at = datetime.now(UTC)
    return row


async def send_verification(db: AsyncSession, user) -> None:
    raw = await issue(db, user, PURPOSE_VERIFY_EMAIL, _VERIFY_TTL)
    link = f"{settings.app_base_url.rstrip('/')}/verify-email?token={raw}"
    await mailer.send(
        db,
        user.org_id,
        kind="verify_email",
        to_email=user.email,
        subject="Confirm your email",
        body=(
            f"Hello {user.name},\n\n"
            f"Please confirm your email address to finish setting up your account:\n"
            f"{link}\n\n"
            f"This link expires in 24 hours. If you didn't create an account, ignore this email."
        ),
    )


async def send_password_reset(db: AsyncSession, user) -> None:
    raw = await issue(db, user, PURPOSE_PASSWORD_RESET, _RESET_TTL)
    link = f"{settings.app_base_url.rstrip('/')}/reset-password?token={raw}"
    await mailer.send(
        db,
        user.org_id,
        kind="password_reset",
        to_email=user.email,
        subject="Reset your password",
        body=(
            f"Hello {user.name},\n\n"
            f"We received a request to reset your password. Set a new one here:\n"
            f"{link}\n\n"
            f"This link expires in 1 hour. If you didn't request this, ignore this email — "
            f"your password is unchanged."
        ),
    )
