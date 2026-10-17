"""One-use OIDC transactions bound to a secret held only by the starting browser."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sso_login_attempt import SsoLoginAttempt

TTL_SECONDS = 600
COOKIE_PATH = "/api/v1/auth/sso/callback"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_name(state: str) -> str:
    # Independent names preserve concurrent login tabs; no state/PKCE in cookies.
    return "iq_oidc_" + digest(state)


async def start(db: AsyncSession, org_id: str, state: str) -> str:
    now = datetime.now(UTC)
    await db.execute(
        delete(SsoLoginAttempt)
        .where(SsoLoginAttempt.org_id == org_id, SsoLoginAttempt.expires_at <= now)
        .execution_options(synchronize_session=False)
    )
    secret = secrets.token_urlsafe(32)
    db.add(
        SsoLoginAttempt(
            org_id=org_id,
            state_hash=digest(state),
            browser_hash=digest(secret),
            expires_at=now + timedelta(seconds=TTL_SECONDS),
        )
    )
    await db.commit()
    return secret


async def consume(db: AsyncSession, org_id: str, state: str, secret: str | None) -> bool:
    if not secret or len(secret) > 128:
        return False
    row = await db.scalar(
        select(SsoLoginAttempt).where(
            SsoLoginAttempt.org_id == org_id, SsoLoginAttempt.state_hash == digest(state)
        )
    )
    if row is None or not hmac.compare_digest(row.browser_hash, digest(secret)):
        return False
    # A conditional write proves one-use even when callbacks arrive together.
    now = datetime.now(UTC)
    used = await db.scalar(
        update(SsoLoginAttempt)
        .where(
            SsoLoginAttempt.org_id == org_id,
            SsoLoginAttempt.id == row.id,
            SsoLoginAttempt.consumed_at.is_(None),
            SsoLoginAttempt.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(SsoLoginAttempt.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()  # consume before external I/O; failures require a fresh login
    return used is not None
