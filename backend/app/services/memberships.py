"""Membership service (Slice 6b).

The write-side of the identity/membership split: every user-creation site keeps a
matching membership, and an existing user can join a second org (multi-org). Still
dual-write — `users.org_id`/`role` remain the active projection until the contract
step; this keeps `memberships` authoritative-in-waiting and consistent.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import Membership
from app.models.user import UserRole


async def get(db: AsyncSession, org_id: str, user_id: str) -> Membership | None:
    return await db.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )


async def for_user(db: AsyncSession, user_id: str) -> list[Membership]:
    """Every membership a user holds (across orgs), newest first."""
    rows = await db.scalars(
        select(Membership)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at.desc())
    )
    return list(rows)


async def ensure(
    db: AsyncSession,
    *,
    org_id: str,
    user_id: str,
    role: UserRole,
    is_expense_approver: bool = False,
    status: str = "active",
    email: str | None = None,
    name: str | None = None,
) -> Membership:
    """Create the (user, org) membership, or update it in place if it exists.
    Idempotent. `email`/`name` snapshot the identity for the roster (only set when
    provided, so a caller that doesn't know them keeps the existing snapshot).
    Flushes to assign the id; the caller commits."""
    m = await get(db, org_id, user_id)
    if m is None:
        m = Membership(
            org_id=org_id,
            user_id=user_id,
            role=role,
            is_expense_approver=is_expense_approver,
            status=status,
            email=email,
            name=name,
        )
        db.add(m)
    else:
        m.role = role
        m.is_expense_approver = is_expense_approver
        m.status = status
        if email is not None:
            m.email = email
        if name is not None:
            m.name = name
    await db.flush()
    return m


async def roster(db: AsyncSession, org_id: str) -> list[Membership]:
    """Every membership in the org (the complete member list — includes members
    currently active in another org). Reads only memberships, so it is org-scoped
    + RLS-safe without touching the User table's tenant scoping."""
    rows = await db.scalars(
        select(Membership).where(Membership.org_id == org_id).order_by(Membership.created_at.asc())
    )
    return list(rows)


async def sync_identity(db: AsyncSession, user_id: str, *, email: str, name: str) -> None:
    """Propagate an email/name change to all of the user's membership snapshots."""
    from sqlalchemy import update as _update

    await db.execute(
        _update(Membership).where(Membership.user_id == user_id).values(email=email, name=name)
    )


async def set_status(db: AsyncSession, org_id: str, user_id: str, status: str) -> None:
    """Set a single membership's status (active|suspended). No-op if not a member."""
    m = await get(db, org_id, user_id)
    if m is not None:
        m.status = status
