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
) -> Membership:
    """Create the (user, org) membership, or update it in place if it exists.
    Idempotent. Flushes to assign the id; the caller commits."""
    m = await get(db, org_id, user_id)
    if m is None:
        m = Membership(
            org_id=org_id,
            user_id=user_id,
            role=role,
            is_expense_approver=is_expense_approver,
            status=status,
        )
        db.add(m)
    else:
        m.role = role
        m.is_expense_approver = is_expense_approver
        m.status = status
    await db.flush()
    return m
