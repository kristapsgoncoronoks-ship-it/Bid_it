"""Session lifecycle + JWT issuance bound to a revocable server-side session.

`start` mints a token whose `jti` is a new session row; `active` validates a jti
on every request; `revoke`/`revoke_all` kill sessions (logout, sign-out-everywhere,
password reset, deactivation). `touch` lazily refreshes last-seen for the
"your sessions" view without a write on every request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token
from app.models.session import Session

_TOUCH_EVERY = timedelta(minutes=5)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:  # SQLite returns naive
        return dt.replace(tzinfo=UTC)
    return dt


def _ttl() -> timedelta:
    return timedelta(minutes=settings.access_token_expire_minutes)


async def start(
    db: AsyncSession, user, *, user_agent: str | None = None, ip: str | None = None
) -> str:
    """Create a session and return a JWT whose `jti` is that session's id. Flushes
    to assign the id; the caller commits."""
    now = datetime.now(UTC)
    s = Session(
        org_id=user.org_id,
        user_id=user.id,
        expires_at=now + _ttl(),
        last_seen_at=now,
        user_agent=(user_agent[:400] if user_agent else None),
        ip=(ip[:64] if ip else None),
    )
    db.add(s)
    await db.flush()
    return create_access_token(user.id, {"org": user.org_id, "jti": s.id})


async def active(db: AsyncSession, jti: str | None) -> Session | None:
    """The session for a jti if it exists and is neither revoked nor expired."""
    if not jti:
        return None
    s = await db.get(Session, jti)
    if s is None or s.revoked_at is not None:
        return None
    if (_aware(s.expires_at) or datetime.now(UTC)) < datetime.now(UTC):
        return None
    return s


async def touch(db: AsyncSession, s: Session) -> None:
    """Refresh last-seen, but at most once per _TOUCH_EVERY (avoids a write per
    request). Best-effort — never raises into the request."""
    now = datetime.now(UTC)
    last = _aware(s.last_seen_at)
    if last is None or last < now - _TOUCH_EVERY:
        s.last_seen_at = now
        await db.commit()


async def revoke(db: AsyncSession, jti: str) -> bool:
    s = await db.get(Session, jti)
    if s is None or s.revoked_at is not None:
        return False
    s.revoked_at = datetime.now(UTC)
    await db.commit()
    return True


async def revoke_bulk(
    db: AsyncSession,
    *,
    user_id: str | None = None,
    org_id: str | None = None,
    except_jti: str | None = None,
) -> int:
    """Bulk-revoke live sessions matching the filters (at least one required).
    Returns the number revoked. THE single revocation mechanism (WO-4) — every
    trigger (logout-everywhere, password reset, org suspension, role change,
    deactivation) funnels through this one UPDATE.

    Deliberately does NOT commit: the caller commits, so the revocation lands
    atomically with the state change that triggered it (and its audit event) —
    a failed trigger can never leave sessions half-revoked, and a revocation
    can never persist without its cause.

    `org_id` scopes the kill to sessions whose ACTIVE org is that tenant: a
    session the user holds in ANOTHER org is untouched, because their standing
    there is unchanged (switching into the affected org re-reads membership
    and hits the per-request gates anyway).
    """
    if user_id is None and org_id is None:
        raise ValueError("revoke_bulk needs user_id and/or org_id")
    stmt = update(Session).where(Session.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC))
    if user_id is not None:
        stmt = stmt.where(Session.user_id == user_id)
    if org_id is not None:
        stmt = stmt.where(Session.org_id == org_id)
    if except_jti:
        stmt = stmt.where(Session.id != except_jti)
    result = await db.execute(stmt)
    return cast(CursorResult, result).rowcount or 0


async def revoke_all(db: AsyncSession, user_id: str, *, except_jti: str | None = None) -> int:
    """Revoke every live session for a user (optionally keeping one). Returns the
    number revoked. Caller need not commit — this commits."""
    n = await revoke_bulk(db, user_id=user_id, except_jti=except_jti)
    await db.commit()
    return n


async def list_active(db: AsyncSession, user_id: str) -> list[Session]:
    now = datetime.now(UTC)
    rows = await db.scalars(
        select(Session)
        .where(
            Session.user_id == user_id,
            Session.revoked_at.is_(None),
            Session.expires_at > now,
        )
        .order_by(Session.last_seen_at.desc())
    )
    return list(rows)
