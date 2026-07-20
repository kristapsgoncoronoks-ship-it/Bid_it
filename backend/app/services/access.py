"""The system matrix — per-role usage limits + quota enforcement.

Four user groups (see `app.core.roles`); the matrix stores the usage limits for
each. Free and paying users are limited by the matrix; admins/sysadmins are not
(their limits default to 0 = unlimited). A sysadmin edits the matrix.

Enforcement today gates **invoice creation** (a persisted, countable unit of
usage): the acting user's role limit vs how many invoices their org has created
in the current calendar month. Uploads have a matrix limit too, ready to wire.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import ASSIGNABLE_ROLES
from app.models.invoice import Invoice
from app.models.role_policy import RolePolicy
from app.models.user import UserRole

# Defaults: (monthly_invoice_limit, monthly_upload_limit). 0 = unlimited.
LIMIT_DEFAULTS: dict[UserRole, tuple[int, int]] = {
    UserRole.user_free: (10, 20),
    UserRole.user: (1000, 2000),
    UserRole.admin: (0, 0),
    UserRole.sysadmin: (0, 0),
}

# Display metadata for the matrix UI.
ROLE_META: dict[UserRole, dict] = {
    UserRole.user_free: {"label": "User-free", "paid": False, "desc": "Non-paying user — limited access."},
    UserRole.user: {"label": "User", "paid": True, "desc": "Paying user."},
    UserRole.admin: {"label": "Admin", "paid": True, "desc": "Access to the admin panel."},
    UserRole.sysadmin: {"label": "Sysadmin", "paid": True, "desc": "All privileges incl. user-rights management."},
}


async def _get_or_seed(db: AsyncSession, role: UserRole) -> RolePolicy:
    row = await db.scalar(select(RolePolicy).where(RolePolicy.role == role.value))
    if row is None:
        inv, up = LIMIT_DEFAULTS[role]
        row = RolePolicy(role=role.value, monthly_invoice_limit=inv, monthly_upload_limit=up)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def matrix(db: AsyncSession) -> list[dict]:
    """The full system matrix — one entry per role (seeded on first read)."""
    out = []
    for role in ASSIGNABLE_ROLES:
        p = await _get_or_seed(db, role)
        meta = ROLE_META[role]
        out.append({
            "role": role.value,
            "label": meta["label"],
            "paid": meta["paid"],
            "description": meta["desc"],
            "monthly_invoice_limit": p.monthly_invoice_limit,
            "monthly_upload_limit": p.monthly_upload_limit,
        })
    return out


async def set_limits(db: AsyncSession, role: UserRole, invoice_limit: int, upload_limit: int) -> dict:
    p = await _get_or_seed(db, role)
    p.monthly_invoice_limit = max(0, int(invoice_limit))
    p.monthly_upload_limit = max(0, int(upload_limit))
    await db.commit()
    await db.refresh(p)
    meta = ROLE_META[role]
    return {
        "role": role.value, "label": meta["label"], "paid": meta["paid"], "description": meta["desc"],
        "monthly_invoice_limit": p.monthly_invoice_limit, "monthly_upload_limit": p.monthly_upload_limit,
    }


def _month_start() -> datetime:
    today = date.today()
    return datetime(today.year, today.month, 1, tzinfo=timezone.utc)


async def invoice_limit_for(db: AsyncSession, role: UserRole) -> int:
    return (await _get_or_seed(db, role)).monthly_invoice_limit


async def invoices_this_month(db: AsyncSession, org_id: str) -> int:
    return await db.scalar(
        select(func.count(Invoice.id)).where(
            Invoice.org_id == org_id, Invoice.created_at >= _month_start()
        )
    ) or 0


async def enforce_invoice_quota(db: AsyncSession, org_id: str, role: UserRole) -> None:
    """Raise 402 if the acting role's monthly invoice limit is reached."""
    limit = await invoice_limit_for(db, role)
    if limit <= 0:
        return  # unlimited
    used = await invoices_this_month(db, org_id)
    if used >= limit:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Monthly invoice limit reached ({used}/{limit}) for your access level. "
            "Upgrade your plan or ask an admin to raise the limit.",
        )


async def usage(db: AsyncSession, org_id: str, role: UserRole) -> dict:
    limit = await invoice_limit_for(db, role)
    used = await invoices_this_month(db, org_id)
    return {
        "role": role.value,
        "invoices_used": used,
        "invoice_limit": limit,
        "invoices_remaining": (max(0, limit - used) if limit > 0 else None),
        "unlimited": limit <= 0,
    }
