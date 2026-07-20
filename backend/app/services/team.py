"""Team management within a tenant: members + token-based invitations."""
from __future__ import annotations

import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.invitation import Invitation
from app.models.user import User, UserRole
from app.services import plans


async def list_members(db: AsyncSession, org_id: str) -> list[User]:
    rows = await db.scalars(
        select(User).where(User.org_id == org_id).order_by(User.created_at)
    )
    return list(rows)


async def sysadmin_count(db: AsyncSession, org_id: str) -> int:
    return await db.scalar(
        select(func.count(User.id)).where(
            User.org_id == org_id, User.role == UserRole.sysadmin, User.is_active.is_(True)
        )
    ) or 0


async def open_invitation_count(db: AsyncSession, org_id: str) -> int:
    return await db.scalar(
        select(func.count(Invitation.id)).where(
            Invitation.org_id == org_id, Invitation.accepted.is_(False)
        )
    ) or 0


async def create_invitation(db: AsyncSession, org_id: str, email: str, role: UserRole, invited_by: str) -> Invitation:
    inv = Invitation(
        org_id=org_id, email=email.lower(), role=role,
        token=secrets.token_urlsafe(24), invited_by=invited_by,
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    return inv


async def list_invitations(db: AsyncSession, org_id: str) -> list[Invitation]:
    rows = await db.scalars(
        select(Invitation).where(Invitation.org_id == org_id, Invitation.accepted.is_(False))
        .order_by(Invitation.created_at.desc())
    )
    return list(rows)


async def accept_invitation(db: AsyncSession, token: str, name: str, password: str) -> tuple[User, str] | None:
    """Returns (user, org_id) on success, or None if token is invalid/used."""
    inv = await db.scalar(select(Invitation).where(Invitation.token == token, Invitation.accepted.is_(False)))
    if inv is None:
        return None
    user = User(
        org_id=inv.org_id, email=inv.email, name=name,
        hashed_password=hash_password(password), role=inv.role,
    )
    db.add(user)
    inv.accepted = True
    await db.commit()
    await db.refresh(user)
    return user, inv.org_id


async def get_member(db: AsyncSession, org_id: str, user_id: str) -> User | None:
    return await db.scalar(select(User).where(User.id == user_id, User.org_id == org_id))
