"""Durable one-way work exports; external calendar acknowledgments are evidence.

Bid_it owns scheduling. Remote attendees are never added, and manual provider
edits are not imported. An uncertain create is reconciled by its persisted
identity rather than blindly submitted a second time. Every handler rechecks
the connection owner and current assignment before exporting anything.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import authz, keyvault
from app.core.config import settings
from app.core.identity import principal
from app.core.tenant import (
    apply_db_tenant,
    get_current_actor,
    reset_current_org,
    set_current_actor,
    set_current_org,
)
from app.models.calendar_connection import (
    CalendarConnection,
    CalendarEventMapping,
    CalendarOAuthAttempt,
)
from app.models.costing import Project
from app.models.job import Job
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.project_assignment import ProjectAssignment
from app.models.session import Session
from app.models.user import User
from app.services import audit, jobs
from app.services import calendar_providers as providers

PROVIDERS = ("google", "outlook")
SYNC_JOB = "calendar.sync"
OAUTH_COOKIE = "bidit_calendar_oauth"
OAUTH_SECONDS = 600
PAGE_SIZE = 100
_Result = TypeVar("_Result")


class CalendarError(ValueError):
    """A sanitized integration refusal suitable for an authenticated caller."""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aad(row: CalendarConnection) -> str:
    return f"calendar:{row.org_id}:{row.user_id}:{row.provider}:{row.id}:{row.generation}"


def _attempt_aad(row: CalendarOAuthAttempt) -> str:
    return f"calendar-oauth:{row.org_id}:{row.user_id}:{row.provider}:{row.id}"


def _valid_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise CalendarError("Unknown calendar provider.")


def configured_reason(provider: str) -> str | None:
    _valid_provider(provider)
    prefix = "google_calendar" if provider == "google" else "outlook_calendar"
    if not getattr(settings, f"{prefix}_client_id", None) or not getattr(
        settings, f"{prefix}_client_secret", None
    ):
        return "The workspace operator must configure this provider's OAuth application."
    for value in (settings.api_public_base_url, getattr(settings, "calendar_public_app_url", "")):
        url = urlsplit(value)
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        development = settings.environment != "production"
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not (url.scheme == "https" or (development and local and url.scheme == "http"))
        ):
            return "Configure public HTTPS application and callback URLs before connecting."
    return None


def callback_uri(provider: str) -> str:
    _valid_provider(provider)
    return f"{settings.api_public_base_url.rstrip('/')}{settings.api_v1_prefix}/schedule/calendar-connections/{provider}/callback"


def return_uri(*, success: bool, pending: bool = False) -> str:
    base = getattr(settings, "calendar_public_app_url", "http://localhost:5173").rstrip("/")
    state = "connected" if success else "pending" if pending else "failed"
    return f"{base}/schedule?calendar_sync={state}"


async def _active_owner(db: AsyncSession, org_id: str, user_id: str) -> bool:
    user = await db.get(User, user_id, populate_existing=True)
    org = await db.get(Organization, org_id, populate_existing=True)
    member = await db.scalar(
        select(Membership)
        .where(Membership.org_id == org_id, Membership.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    return bool(
        user
        and user.is_active
        and org
        and org.status == "active"
        and member
        and member.status == "active"
        and authz.has(principal(user, member), authz.Permission.INVOICE_READ)
    )


async def _connection(
    db: AsyncSession, org_id: str, user_id: str, provider: str, *, lock: bool = False
):
    query = (
        select(CalendarConnection)
        .where(
            CalendarConnection.org_id == org_id,
            CalendarConnection.user_id == user_id,
            CalendarConnection.provider == provider,
        )
        .execution_options(populate_existing=True)
    )
    return await db.scalar(query.with_for_update() if lock else query)


async def status(db: AsyncSession, org_id: str, user_id: str) -> dict:
    result = []
    for provider in PROVIDERS:
        reason = configured_reason(provider)
        row = await _connection(db, org_id, user_id, provider)
        connection = None
        if row:
            counts = dict(
                (
                    await db.execute(
                        select(CalendarEventMapping.status, func.count())
                        .where(
                            CalendarEventMapping.org_id == org_id,
                            CalendarEventMapping.connection_id == row.id,
                        )
                        .group_by(CalendarEventMapping.status)
                    )
                ).all()
            )
            connection = {
                "id": row.id,
                "status": row.status,
                "calendar_name": row.calendar_name,
                "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
                "error": row.error,
                "pending": sum(counts.get(s, 0) for s in ("queued", "syncing"))
                + int(row.status == "connecting" and not row.calendar_id),
                "failed": sum(counts.get(s, 0) for s in ("failed", "unknown", "blocked")),
            }
        result.append(
            {
                "provider": provider,
                "configured": reason is None,
                "reason": reason,
                "connection": connection,
            }
        )
    return {"providers": result}


async def begin_oauth(
    db: AsyncSession, org_id: str, user_id: str, session_id: str, provider: str
) -> tuple[str, str]:
    reason = configured_reason(provider)
    if reason:
        raise CalendarError(reason)
    now = datetime.now(UTC)
    conn = await _connection(db, org_id, user_id, provider, lock=True)
    if conn is None:
        org = await db.get(Organization, org_id)
        conn = CalendarConnection(
            id=str(uuid.uuid4()),
            org_id=org_id,
            user_id=user_id,
            provider=provider,
            generation=1,
            calendar_name=f"{org.name if org else 'Workspace'} work schedule"[:120],
            status="authorizing",
            setup_started=False,
        )
        conn.calendar_marker = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"bidit-calendar:{conn.id}:{conn.generation}")
        )
        try:
            async with db.begin_nested():
                db.add(conn)
                await db.flush()
        except IntegrityError:
            conn = await _connection(db, org_id, user_id, provider, lock=True)
            if conn is None:
                raise
    if conn.status == "disconnected":
        # Reconnection after explicit disconnect is a new export generation.
        # The previously exported provider calendar remains user-owned.
        conn.calendar_id, conn.account_hash, conn.setup_started = None, None, False
        conn.calendar_marker = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"bidit-calendar:{conn.id}:{conn.generation}")
        )
        conn.status, conn.error = "authorizing", None
        await db.execute(
            delete(CalendarEventMapping).where(
                CalendarEventMapping.org_id == org_id, CalendarEventMapping.connection_id == conn.id
            )
        )
    await db.execute(
        delete(CalendarOAuthAttempt).where(
            CalendarOAuthAttempt.org_id == org_id,
            CalendarOAuthAttempt.user_id == user_id,
            CalendarOAuthAttempt.expires_at < now,
        )
    )
    count = await db.scalar(
        select(func.count())
        .select_from(CalendarOAuthAttempt)
        .where(
            CalendarOAuthAttempt.org_id == org_id,
            CalendarOAuthAttempt.user_id == user_id,
            CalendarOAuthAttempt.consumed_at.is_(None),
        )
    )
    if count and count >= 10:
        raise CalendarError(
            "Too many unfinished calendar connections. Wait ten minutes and try again."
        )
    state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    row = CalendarOAuthAttempt(
        id=str(uuid.uuid4()),
        org_id=org_id,
        user_id=user_id,
        session_id=session_id,
        provider=provider,
        connection_id=conn.id,
        connection_generation=conn.generation,
        state_hash=_hash(state),
        browser_hash=_hash(browser),
        expires_at=now + timedelta(seconds=OAUTH_SECONDS),
        sealed_verifier="",
    )
    row.sealed_verifier = keyvault.seal(verifier, aad=_attempt_aad(row))
    db.add(row)
    await audit.record(
        db, "calendar.authorize", target_type="user", target_id=user_id, meta={"provider": provider}
    )
    await db.commit()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return providers.authorization_url(provider, state, challenge, callback_uri(provider)), browser


def _seal_tokens(conn: CalendarConnection, data: dict) -> None:
    access, refresh = data.get("access_token"), data.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        raise CalendarError(
            "The provider did not grant renewable calendar access. Connect again and grant offline access."
        )
    expires = int(data.get("expires_in", 3600))
    if not 0 < expires <= 366 * 86400 or max(len(access), len(refresh)) > 16000:
        raise CalendarError("The provider returned invalid calendar credentials.")
    conn.sealed_tokens = keyvault.seal(
        json.dumps(
            {
                "access_token": access,
                "refresh_token": refresh,
                "expires_at": (datetime.now(UTC) + timedelta(seconds=expires)).isoformat(),
            }
        ),
        aad=_aad(conn),
    )


async def complete_oauth(
    db: AsyncSession, provider: str, state: str, browser: str, code: str | None
) -> bool:
    _valid_provider(provider)
    if (
        configured_reason(provider)
        or not browser
        or not state
        or len(state) > 200
        or (code and len(code) > 8192)
    ):
        raise CalendarError(
            "Calendar authorization could not be verified. Start again from work planning."
        )
    attempt = await db.scalar(
        select(CalendarOAuthAttempt).where(
            CalendarOAuthAttempt.state_hash == _hash(state),
            CalendarOAuthAttempt.provider == provider,
        )
    )
    now = datetime.now(UTC)
    if not attempt or not secrets.compare_digest(attempt.browser_hash, _hash(browser)):
        raise CalendarError(
            "Calendar authorization could not be verified. Start again from work planning."
        )
    org_token = set_current_org(attempt.org_id)
    actor = get_current_actor()
    try:
        await apply_db_tenant(db)
        session = await db.get(Session, attempt.session_id)
        if (
            attempt.consumed_at
            or _utc(attempt.expires_at) <= now
            or not session
            or session.revoked_at is not None
            or _utc(session.expires_at) <= now
            or session.user_id != attempt.user_id
            or session.org_id != attempt.org_id
            or not await _active_owner(db, attempt.org_id, attempt.user_id)
        ):
            raise CalendarError(
                "Calendar authorization expired or the workspace changed. Start again."
            )
        claimed = await db.scalar(
            update(CalendarOAuthAttempt)
            .where(
                CalendarOAuthAttempt.id == attempt.id,
                CalendarOAuthAttempt.org_id == attempt.org_id,
                CalendarOAuthAttempt.consumed_at.is_(None),
                CalendarOAuthAttempt.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(CalendarOAuthAttempt.id)
            .execution_options(synchronize_session=False)
        )
        if not claimed:
            raise CalendarError("Calendar authorization was already used. Start again.")
        set_current_actor(attempt.user_id, None)
        try:
            verifier = keyvault.unseal(attempt.sealed_verifier, aad=_attempt_aad(attempt))
        except keyvault.KeyvaultError:
            raise CalendarError(
                "Calendar authorization could not be verified. Start again."
            ) from None
        await db.commit()  # One-use claim is durable before token exchange.
        if not code:
            raise CalendarError("Calendar authorization was declined. Connect again when ready.")
        data = await providers.exchange_code(provider, code, verifier, callback_uri(provider))
        account_hash = _hash(
            provider + ":" + await providers.account_key(provider, data["access_token"])
        )
        # Revalidate the originating session after external I/O; offboarding or
        # switching a session while the provider was open cannot create a link.
        session = await db.scalar(
            select(Session)
            .where(Session.id == attempt.session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        live_attempt = await db.scalar(
            select(CalendarOAuthAttempt.id).where(
                CalendarOAuthAttempt.id == attempt.id, CalendarOAuthAttempt.org_id == attempt.org_id
            )
        )
        if (
            not session
            or not live_attempt
            or session.revoked_at
            or session.user_id != attempt.user_id
            or session.org_id != attempt.org_id
            or _utc(session.expires_at) <= datetime.now(UTC)
            or not await _active_owner(db, attempt.org_id, attempt.user_id)
        ):
            raise CalendarError("The originating session is no longer active.")
        conn = await _connection(db, attempt.org_id, attempt.user_id, provider, lock=True)
        if (
            not conn
            or conn.id != attempt.connection_id
            or conn.generation != attempt.connection_generation
            or conn.status == "disconnected"
        ):
            raise CalendarError(
                "This calendar authorization was disconnected or replaced. Start again."
            )
        if conn.account_hash and conn.account_hash != account_hash:
            raise CalendarError(
                "This is a different calendar account. Disconnect the existing connection first."
            )
        conn.account_hash = account_hash
        conn.generation += 1
        _seal_tokens(conn, data)
        conn.error = None
        conn.status = "connecting" if not conn.calendar_id else "connected"
        await audit.record(
            db,
            "calendar.connect",
            target_type="user",
            target_id=conn.user_id,
            meta={"provider": provider},
        )
        await _enqueue_setup(db, conn)
        await db.commit()
        result = await _finish_setup(db, conn.id, conn.org_id, conn.generation)
        return bool(result["synced"])
    finally:
        set_current_actor(*actor)
        reset_current_org(org_token)


async def _access_token(db: AsyncSession, conn: CalendarConnection, *, force: bool = False) -> str:
    if not conn.sealed_tokens:
        raise providers.ProviderError("calendar_credentials_unavailable", auth_required=True)
    try:
        tokens = json.loads(keyvault.unseal(conn.sealed_tokens, aad=_aad(conn)))
        expiry = datetime.fromisoformat(tokens["expires_at"])
        if not isinstance(tokens["access_token"], str) or not isinstance(
            tokens["refresh_token"], str
        ):
            raise ValueError("invalid token")
    except (keyvault.KeyvaultError, ValueError, KeyError, TypeError):
        raise providers.ProviderError(
            "calendar_credentials_unavailable", auth_required=True
        ) from None
    if force or _utc(expiry) <= datetime.now(UTC) + timedelta(seconds=60):
        data = await providers.refresh_tokens(conn.provider, tokens["refresh_token"])
        data["refresh_token"] = data.get("refresh_token") or tokens["refresh_token"]
        try:
            _seal_tokens(conn, data)
        except (CalendarError, ValueError, TypeError):
            raise providers.ProviderError(
                "calendar_credentials_unavailable", auth_required=True
            ) from None
        await db.flush()
        return data["access_token"]
    return tokens["access_token"]


async def _authorized(
    db: AsyncSession, conn: CalendarConnection, operation: Callable[[str], Awaitable[_Result]]
) -> _Result:
    """Refresh once on a definite 401, then require an explicit reconnect."""
    access = await _access_token(db, conn)
    try:
        return await operation(access)
    except providers.ProviderError as exc:
        if not exc.auth_required:
            raise
    return await operation(await _access_token(db, conn, force=True))


async def _finish_setup(db: AsyncSession, connection_id: str, org_id: str, generation: int) -> dict:
    conn = await db.scalar(
        select(CalendarConnection)
        .where(CalendarConnection.id == connection_id, CalendarConnection.org_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not conn or conn.status == "disconnected" or conn.generation != generation:
        return {"synced": False, "reason": "disconnected"}
    if not await _active_owner(db, org_id, conn.user_id):
        conn.status, conn.error, conn.sealed_tokens = "disconnected", "access_revoked", None
        conn.generation += 1
        await db.commit()
        return {"synced": False, "reason": "access_revoked"}
    if not conn.calendar_id:
        attempted = conn.setup_started
        conn.setup_started = True
        await db.commit()  # Persist uncertain-create boundary before external I/O.
        conn = await db.scalar(
            select(CalendarConnection)
            .where(CalendarConnection.id == connection_id, CalendarConnection.org_id == org_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            conn is None
            or conn.status == "disconnected"
            or conn.generation != generation
            or not await _active_owner(db, org_id, conn.user_id)
        ):
            return {"synced": False, "reason": "disconnected"}
        try:
            marker = conn.calendar_marker

            async def setup(access: str) -> str | None:
                found = await providers.find_calendar(
                    conn.provider, access, conn.calendar_name, marker=marker
                )
                if found or attempted:
                    return found
                return await providers.create_calendar(
                    conn.provider, access, name=conn.calendar_name, marker=marker
                )

            found = await _authorized(db, conn, setup)
            if not found and attempted:
                conn.status, conn.error = "setup_unknown", "calendar_setup_unknown"
                await db.commit()
                return {"synced": False, "reason": "calendar_setup_unknown"}
            conn.calendar_id = found
        except providers.ProviderError as exc:
            conn.status = "needs_reconnect" if exc.auth_required else "setup_unknown"
            conn.error = exc.code
            if not exc.ambiguous:
                conn.setup_started = False
            await db.commit()
            return {"synced": False, "reason": conn.error}
    conn.status, conn.error = "connected", None
    await _enqueue_reconcile(db, conn)
    await db.commit()
    return {"synced": True, "reason": "connected"}


async def disconnect(db: AsyncSession, org_id: str, user_id: str, provider: str) -> None:
    _valid_provider(provider)
    conn = await _connection(db, org_id, user_id, provider, lock=True)
    if conn:
        conn.generation += 1
        conn.status, conn.sealed_tokens, conn.error = "disconnected", None, None
        await db.execute(
            update(CalendarEventMapping)
            .where(
                CalendarEventMapping.org_id == org_id, CalendarEventMapping.connection_id == conn.id
            )
            .values(status="blocked", error="disconnected")
        )
        await audit.record(
            db,
            "calendar.disconnect",
            target_type="user",
            target_id=user_id,
            meta={"provider": provider},
        )
    await db.execute(
        delete(CalendarOAuthAttempt).where(
            CalendarOAuthAttempt.org_id == org_id,
            CalendarOAuthAttempt.user_id == user_id,
            CalendarOAuthAttempt.provider == provider,
        )
    )
    await db.commit()


async def retry(db: AsyncSession, org_id: str, user_id: str, provider: str) -> None:
    _valid_provider(provider)
    if configured_reason(provider):
        raise CalendarError("The provider is not configured.")
    conn = await _connection(db, org_id, user_id, provider, lock=True)
    if not conn or conn.status in {"disconnected", "needs_reconnect"}:
        raise CalendarError("Connect this calendar before retrying synchronization.")
    if conn.status == "needs_configuration":
        conn.status, conn.error = "connected" if conn.calendar_id else "connecting", None
    if not conn.calendar_id:
        await _enqueue_setup(db, conn)
    else:
        await _enqueue_reconcile(db, conn)
    await audit.record(
        db, "calendar.retry", target_type="user", target_id=user_id, meta={"provider": provider}
    )
    await db.commit()


async def _active_chain(db: AsyncSession, conn: CalendarConnection, purpose: str) -> bool:
    """Call while holding the connection lock, so concurrent retries coalesce."""
    return (
        await db.scalar(
            select(Job.id)
            .where(
                Job.org_id == conn.org_id,
                Job.kind == SYNC_JOB,
                Job.status.in_(("queued", "running")),
                Job.idempotency_key.like(f"calendar.{purpose}:{conn.id}:{conn.generation}:%"),
            )
            .limit(1)
        )
        is not None
    )


async def _enqueue_setup(db: AsyncSession, conn: CalendarConnection) -> None:
    if await _active_chain(db, conn, "setup"):
        return
    await jobs.enqueue(
        db,
        SYNC_JOB,
        {"connection_id": conn.id, "generation": conn.generation, "setup": True},
        org_id=conn.org_id,
        idempotency_key=f"calendar.setup:{conn.id}:{conn.generation}:{uuid.uuid4().hex}",
        commit=False,
    )


async def _enqueue_reconcile(db: AsyncSession, conn: CalendarConnection) -> None:
    if await _active_chain(db, conn, "reconcile"):
        return
    recovery = uuid.uuid4().hex
    await jobs.enqueue(
        db,
        SYNC_JOB,
        {
            "connection_id": conn.id,
            "generation": conn.generation,
            "reconcile": True,
            "recovery": recovery,
        },
        org_id=conn.org_id,
        idempotency_key=f"calendar.reconcile:{conn.id}:{conn.generation}:{recovery}",
        commit=False,
    )


async def _queue_mapping(
    db: AsyncSession,
    conn: CalendarConnection,
    row: ProjectAssignment | None,
    assignment_id: str,
    *,
    recovery: str | None = None,
) -> None:
    mapping = await db.scalar(
        select(CalendarEventMapping).where(
            CalendarEventMapping.org_id == conn.org_id,
            CalendarEventMapping.connection_id == conn.id,
            CalendarEventMapping.assignment_id == assignment_id,
        )
    )
    revision = (
        getattr(row, "revision", 1) if row else (mapping.desired_revision + 1 if mapping else 1)
    )
    if mapping is None:
        mapping = CalendarEventMapping(
            id=str(uuid.uuid4()),
            org_id=conn.org_id,
            connection_id=conn.id,
            assignment_id=assignment_id,
            stable_id=str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"bidit:{conn.id}:{conn.generation}:{assignment_id}")
            ),
            desired_revision=revision,
            synced_revision=0,
            status="queued",
            create_started=False,
        )
        try:
            async with db.begin_nested():
                db.add(mapping)
                await db.flush()
        except IntegrityError:
            mapping = await db.scalar(
                select(CalendarEventMapping).where(
                    CalendarEventMapping.org_id == conn.org_id,
                    CalendarEventMapping.connection_id == conn.id,
                    CalendarEventMapping.assignment_id == assignment_id,
                )
            )
            if mapping is None:
                raise
    mapping.desired_revision = max(mapping.desired_revision, revision)
    if recovery or mapping.synced_revision < mapping.desired_revision:
        mapping.status, mapping.error = "queued", None
    suffix = f":{recovery}" if recovery else ""
    await jobs.enqueue(
        db,
        SYNC_JOB,
        {
            "connection_id": conn.id,
            "generation": conn.generation,
            "assignment_id": assignment_id,
            "recovery": bool(recovery),
        },
        org_id=conn.org_id,
        idempotency_key=f"calendar.event:{conn.id}:{conn.generation}:{assignment_id}:{revision}{suffix}",
        commit=False,
    )


async def enqueue_assignment_sync(db: AsyncSession, row: ProjectAssignment) -> None:
    """Called inside the assignment/audit transaction; no provider or commit."""
    old_ids = select(CalendarEventMapping.connection_id).where(
        CalendarEventMapping.org_id == row.org_id, CalendarEventMapping.assignment_id == row.id
    )
    connections = await db.scalars(
        select(CalendarConnection).where(
            CalendarConnection.org_id == row.org_id,
            CalendarConnection.status == "connected",
            (
                (CalendarConnection.user_id == row.assignee_user_id)
                | CalendarConnection.id.in_(old_ids)
            ),
        )
    )
    for conn in connections:
        await _queue_mapping(db, conn, row, row.id)


async def _reconcile(db: AsyncSession, conn: CalendarConnection, payload: dict) -> dict:
    cursor = str(payload.get("cursor") or "")
    now = datetime.now(UTC)
    assignment_ids = select(ProjectAssignment.id).where(
        ProjectAssignment.org_id == conn.org_id,
        ProjectAssignment.assignee_user_id == conn.user_id,
        ProjectAssignment.ends_at >= now - timedelta(days=30),
        ProjectAssignment.starts_at <= now + timedelta(days=365),
    )
    mapped_ids = select(CalendarEventMapping.assignment_id).where(
        CalendarEventMapping.org_id == conn.org_id, CalendarEventMapping.connection_id == conn.id
    )
    union = assignment_ids.union(mapped_ids).subquery()
    ids = list(
        (
            await db.scalars(
                select(union.c[0])
                .where(union.c[0] > cursor)
                .order_by(union.c[0])
                .limit(PAGE_SIZE + 1)
            )
        ).all()
    )
    for source_id in ids[:PAGE_SIZE]:
        row = await db.scalar(
            select(ProjectAssignment).where(
                ProjectAssignment.org_id == conn.org_id, ProjectAssignment.id == source_id
            )
        )
        await _queue_mapping(db, conn, row, source_id, recovery=payload.get("recovery"))
    if len(ids) > PAGE_SIZE:
        await jobs.enqueue(
            db,
            SYNC_JOB,
            {**payload, "cursor": ids[PAGE_SIZE - 1]},
            org_id=conn.org_id,
            idempotency_key=f"calendar.reconcile:{conn.id}:{conn.generation}:{payload.get('recovery')}:{ids[PAGE_SIZE - 1]}",
            commit=False,
        )
    await db.commit()
    return {"queued": min(len(ids), PAGE_SIZE), "continued": len(ids) > PAGE_SIZE}


def event_payload(row: ProjectAssignment, project: Project | None) -> dict:
    zone_name = getattr(row, "time_zone", "UTC") or "UTC"
    zone = ZoneInfo(zone_name)
    starts, ends = _utc(row.starts_at), _utc(row.ends_at)
    if row.all_day:
        start, end = (
            {"date": starts.astimezone(zone).date().isoformat(), "timeZone": zone_name},
            {"date": ends.astimezone(zone).date().isoformat(), "timeZone": zone_name},
        )
    else:
        start = {"dateTime": starts.isoformat(), "timeZone": zone_name}
        end = {"dateTime": ends.isoformat(), "timeZone": zone_name}
    return {
        "summary": f"{project.code} · {project.name}" if project else "Work assignment",
        "description": row.note or "",
        "start": start,
        "end": end,
    }


async def run_calendar_sync(db: AsyncSession, payload: dict, job: Job) -> dict:
    """One managed export; fresh tenant/member checks precede every I/O phase."""
    connection_id = str(payload.get("connection_id") or "")
    conn = await db.scalar(
        select(CalendarConnection)
        .where(CalendarConnection.org_id == job.org_id, CalendarConnection.id == connection_id)
        .execution_options(populate_existing=True)
    )
    if not conn or conn.status in {"disconnected", "needs_reconnect"}:
        return {"synced": False, "reason": "disconnected"}
    if payload.get("generation") != conn.generation:
        return {"synced": False, "reason": "connection_replaced"}
    if configured_reason(conn.provider):
        conn.status, conn.error = "needs_configuration", "provider_not_configured"
        await db.commit()
        return {"synced": False, "reason": "provider_not_configured"}
    if not await _active_owner(db, conn.org_id, conn.user_id):
        conn.status, conn.sealed_tokens, conn.error = "disconnected", None, "access_revoked"
        conn.generation += 1
        await db.commit()
        return {"synced": False, "reason": "access_revoked"}
    if payload.get("setup"):
        return await _finish_setup(db, conn.id, conn.org_id, conn.generation)
    if payload.get("reconcile"):
        return await _reconcile(db, conn, payload)
    source_id = str(payload.get("assignment_id") or "")
    # Consistent lock order with planner writes: assignment, connection, mapping.
    row = await db.scalar(
        select(ProjectAssignment)
        .where(ProjectAssignment.org_id == job.org_id, ProjectAssignment.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    conn = await db.scalar(
        select(CalendarConnection)
        .where(CalendarConnection.org_id == job.org_id, CalendarConnection.id == connection_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    mapping = await db.scalar(
        select(CalendarEventMapping)
        .where(
            CalendarEventMapping.org_id == job.org_id,
            CalendarEventMapping.connection_id == connection_id,
            CalendarEventMapping.assignment_id == source_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not conn
        or not mapping
        or conn.status != "connected"
        or not conn.calendar_id
        or payload.get("generation") != conn.generation
    ):
        return {"synced": False, "reason": "disconnected"}
    now = datetime.now(UTC)
    if (
        mapping.lease_owner
        and mapping.lease_owner != job.id
        and mapping.lease_until
        and _utc(mapping.lease_until) > now
    ):
        raise jobs.RetryAfterError(
            "Calendar synchronization is already running.", retry_after_seconds=30
        )
    if (
        mapping.synced_revision >= mapping.desired_revision
        and mapping.status == "synced"
        and not payload.get("recovery")
    ):
        return {"synced": True, "reason": "already_synced"}
    mapping.lease_owner, mapping.lease_until = job.id, now + timedelta(minutes=5)
    mapping.status = "syncing"
    allow_create = not mapping.create_started
    mapping.create_started = True
    await db.commit()  # Persist operation/lease BEFORE any external event write.
    # Reacquire in the same order after that commit; a reschedule/disconnect may
    # have completed while this job was committing its intent.
    row = await db.scalar(
        select(ProjectAssignment)
        .where(ProjectAssignment.org_id == job.org_id, ProjectAssignment.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    conn = await db.scalar(
        select(CalendarConnection)
        .where(CalendarConnection.org_id == job.org_id, CalendarConnection.id == connection_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    mapping = await db.scalar(
        select(CalendarEventMapping)
        .where(
            CalendarEventMapping.org_id == job.org_id,
            CalendarEventMapping.connection_id == connection_id,
            CalendarEventMapping.assignment_id == source_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not conn
        or not mapping
        or mapping.lease_owner != job.id
        or conn.status != "connected"
        or payload.get("generation") != conn.generation
    ):
        return {"synced": False, "reason": "disconnected"}
    if not await _active_owner(db, conn.org_id, conn.user_id):
        conn.status, conn.sealed_tokens, conn.error = "disconnected", None, "access_revoked"
        conn.generation += 1
        mapping.status, mapping.error = "blocked", "access_revoked"
        mapping.lease_owner, mapping.lease_until = None, None
        await db.commit()
        return {"synced": False, "reason": "access_revoked"}
    try:
        should_remove = (
            row is None or row.assignee_user_id != conn.user_id or row.status == "cancelled"
        )
        project = (
            await db.scalar(
                select(Project).where(Project.org_id == job.org_id, Project.id == row.project_id)
            )
            if row
            else None
        )

        async def export(access: str) -> str | None:
            assert conn.calendar_id is not None
            event_id = mapping.event_id or await providers.find_event(
                conn.provider, access, conn.calendar_id, mapping.stable_id
            )
            if should_remove:
                if event_id:
                    # Preserve discovered identity on a failed/unknown delete,
                    # so a later retry can check the same remote object.
                    mapping.event_id = event_id
                    await providers.delete_event(conn.provider, access, conn.calendar_id, event_id)
                elif not allow_create:
                    # A prior create lacked an acknowledgment. A temporarily
                    # absent lookup cannot prove its late write is cancelled.
                    raise providers.ProviderError("event_removal_unknown", ambiguous=True)
                return None
            assert row is not None
            return await providers.put_event(
                conn.provider,
                access,
                conn.calendar_id,
                event_id,
                mapping.stable_id,
                event_payload(row, project),
                allow_create=allow_create,
            )

        mapping.event_id = await _authorized(db, conn, export)
        if should_remove:
            # Only an authoritative deletion/never-created outcome retires the
            # old identity. Google retains deletion tombstones: returning work
            # to this assignee therefore needs a fresh provider event identity.
            mapping.stable_id = str(uuid.uuid4())
            mapping.create_started = False
        mapping.synced_revision = mapping.desired_revision
        mapping.status, mapping.error = "synced", None
        mapping.last_synced_at = conn.last_synced_at = datetime.now(UTC)
        conn.error = None
        mapping.lease_owner, mapping.lease_until = None, None
        await db.commit()
        return {"synced": True, "removed": should_remove}
    except providers.ProviderError as exc:
        mapping.status = (
            "unknown" if exc.ambiguous or (not allow_create and not mapping.event_id) else "failed"
        )
        mapping.error = exc.code
        if not exc.ambiguous and allow_create:
            mapping.create_started = False
        mapping.lease_owner, mapping.lease_until = None, None
        if exc.auth_required:
            conn.status, conn.error = "needs_reconnect", "calendar_authorization_expired"
        await db.commit()
        if exc.auth_required or exc.permanent:
            raise jobs.PermanentJobError(
                "Calendar synchronization needs attention: " + exc.code
            ) from None
        if exc.retry_after:
            raise jobs.RetryAfterError(
                "Calendar provider deferred synchronization.", retry_after_seconds=exc.retry_after
            ) from None
        raise RuntimeError("Calendar synchronization did not complete: " + exc.code) from None
