"""Team management within a tenant: members + token-based invitations."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.invitation import Invitation
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


async def list_members(db: AsyncSession, org_id: str) -> list[User]:
    rows = await db.scalars(select(User).where(User.org_id == org_id).order_by(User.created_at))
    return list(rows)


async def owner_count(db: AsyncSession, org_id: str) -> int:
    return (
        await db.scalar(
            select(func.count(User.id)).where(
                User.org_id == org_id, User.role == UserRole.owner, User.is_active.is_(True)
            )
        )
        or 0
    )


async def approver_count(db: AsyncSession, org_id: str) -> int:
    return (
        await db.scalar(
            select(func.count(User.id)).where(
                User.org_id == org_id, User.is_expense_approver.is_(True), User.is_active.is_(True)
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


async def accept_invitation(
    db: AsyncSession, token: str, name: str, password: str
) -> tuple[User, str] | None:
    """Returns (user, org_id) on success, or None if token is invalid/used."""
    inv = await db.scalar(
        select(Invitation).where(Invitation.token == token, Invitation.accepted.is_(False))
    )
    if inv is None or invitation_expired(inv):
        return None
    user = User(
        org_id=inv.org_id,
        email=inv.email,
        name=name,
        hashed_password=hash_password(password),
        role=inv.role,
    )
    db.add(user)
    inv.accepted = True
    await db.commit()
    await db.refresh(user)
    return user, inv.org_id


async def get_member(db: AsyncSession, org_id: str, user_id: str) -> User | None:
    return await db.scalar(select(User).where(User.id == user_id, User.org_id == org_id))
