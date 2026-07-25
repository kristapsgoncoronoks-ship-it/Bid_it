"""Team management within a tenant: members + token-based invitations."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.user import User, UserRole

INVITE_TTL = timedelta(days=14)


def invitation_expired(inv: Invitation) -> bool:
    """True if the invite has a past expiry (a NULL expiry never expires)."""
    exp = inv.expires_at
    if exp is None:
        return False
    if exp.tzinfo is None:  # SQLite returns naive
        exp = exp.replace(tzinfo=UTC)
    return exp < datetime.now(UTC)


async def list_members(db: AsyncSession, org_id: str) -> list[Membership]:
    # Membership-driven roster (Slice 6e): complete (includes members currently
    # active in another org) and RLS-safe, without de-scoping the User table.
    from app.services import memberships

    return await memberships.roster(db, org_id)


async def owner_count(db: AsyncSession, org_id: str) -> int:
    # Membership-based (Slice 6d): counts owners of THIS org regardless of which
    # org each is currently active in.
    return (
        await db.scalar(
            select(func.count(Membership.id)).where(
                Membership.org_id == org_id,
                Membership.role == UserRole.owner,
                Membership.status == "active",
            )
        )
        or 0
    )


async def approver_count(db: AsyncSession, org_id: str) -> int:
    return (
        await db.scalar(
            select(func.count(Membership.id)).where(
                Membership.org_id == org_id,
                Membership.is_expense_approver.is_(True),
                Membership.status == "active",
            )
        )
        or 0
    )


async def open_invitation_count(db: AsyncSession, org_id: str) -> int:
    return (
        await db.scalar(
            select(func.count(Invitation.id)).where(
                Invitation.org_id == org_id, Invitation.accepted.is_(False)
            )
        )
        or 0
    )


async def create_invitation(
    db: AsyncSession, org_id: str, email: str, role: UserRole, invited_by: str
) -> Invitation:
    inv = Invitation(
        org_id=org_id,
        email=email.lower(),
        role=role,
        token=secrets.token_urlsafe(24),
        invited_by=invited_by,
        expires_at=datetime.now(UTC) + INVITE_TTL,
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    return inv


async def send_invitation_email(
    db: AsyncSession, inv: Invitation, *, org_name: str, invited_by: str | None = None
) -> None:
    """Email the invitee their accept link (recorded to the outbox; delivered when
    SMTP is configured; never raises). Kind 'invitation' is excluded from the
    invoice-delivery log."""
    from app.core.config import settings
    from app.services import mailer

    link = f"{settings.app_base_url.rstrip('/')}/accept-invite?token={inv.token}"
    await mailer.send(
        db,
        inv.org_id,
        kind="invitation",
        to_email=inv.email,
        subject=f"You're invited to join {org_name} on InvoiceIQ",
        body=(
            f"Hello,\n\n"
            f"{invited_by or 'A colleague'} has invited you to join {org_name} on "
            f"InvoiceIQ as {inv.role.value}.\n\n"
            f"Accept your invitation:\n{link}\n\n"
            f"This invitation expires in 14 days. If you weren't expecting it, ignore this email."
        ),
    )


async def list_invitations(db: AsyncSession, org_id: str) -> list[Invitation]:
    rows = await db.scalars(
        select(Invitation)
        .where(Invitation.org_id == org_id, Invitation.accepted.is_(False))
        .order_by(Invitation.created_at.desc())
    )
    return list(rows)


class InviteAuthError(Exception):
    """The invite email already has an account and the supplied password is wrong."""


async def accept_invitation(
    db: AsyncSession, token: str, name: str, password: str
) -> tuple[User, str] | None:
    """Accept an invite. For a NEW email, creates the account. For an EXISTING
    account (multi-org join), the password must match that account — then a
    membership in the inviting org is added. Either way a membership is written
    (Slice 6b). Returns (user, org_id), None if invalid/expired, or raises
    InviteAuthError if an existing account's password is wrong."""
    from app.services import memberships

    inv = await db.scalar(
        select(Invitation).where(Invitation.token == token, Invitation.accepted.is_(False))
    )
    if inv is None or invitation_expired(inv):
        return None

    existing = await db.scalar(select(User).where(User.email == inv.email))
    if existing is not None:
        # An existing person joining another org must prove the account is theirs.
        if not verify_password(password, existing.hashed_password):
            raise InviteAuthError()
        user = existing
    else:
        user = User(
            org_id=inv.org_id,
            email=inv.email,
            name=name,
            hashed_password=hash_password(password),
            role=inv.role,
        )
        db.add(user)
        await db.flush()

    await memberships.ensure(
        db, org_id=inv.org_id, user_id=user.id, role=inv.role, email=user.email, name=user.name
    )
    inv.accepted = True
    await db.commit()
    await db.refresh(user)
    return user, inv.org_id


async def revoke_member_sessions(
    db: AsyncSession, org_id: str, user_id: str, *, trigger: str
) -> int:
    """Kill the member's live sessions in THIS org so a role change or a
    deactivation forces re-authentication immediately (WO-4) — a live token must
    never keep exercising a role the member no longer holds.

    Scoped to the org on purpose: a session the member holds with ANOTHER org
    active is untouched (their standing there is unchanged, and switching back
    into this org re-reads the membership + per-request gates). Audited as
    `session.revoked_bulk` with the trigger and count. The caller commits, so
    the revocation is atomic with the membership change and its audit trail.
    """
    from app.services import audit, sessions

    n = await sessions.revoke_bulk(db, user_id=user_id, org_id=org_id)
    await audit.record(
        db,
        audit.A.SESSION_REVOKE_BULK,
        target_type="user",
        target_id=user_id,
        meta={"trigger": trigger, "count": n},
    )
    return n


async def get_member(db: AsyncSession, org_id: str, user_id: str) -> Membership | None:
    # The member's MEMBERSHIP in this org (Slice 6e) — works even if the member is
    # currently active in another org.
    from app.services import memberships

    return await memberships.get(db, org_id, user_id)
