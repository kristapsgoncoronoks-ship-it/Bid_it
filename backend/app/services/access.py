"""The system matrix — per-role usage limits + quota enforcement.

Four user groups (see `app.core.roles`); the matrix stores the usage limits for
each. Free and paying users are limited by the matrix; admins/sysadmins are not
(their limits default to 0 = unlimited). A sysadmin edits the matrix.

Enforcement gates **invoice creation** (counted off the invoices table) and
**document uploads** (metered in `usage_counters`, since a parse has no fact
row of its own). Both compare the acting role's monthly limit to the tenant's
current-month usage.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import ASSIGNABLE_ROLES
from app.models.invoice import Invoice
from app.models.role_policy import RolePolicy
from app.models.usage import UsageCounter
from app.models.user import UserRole

# Defaults: (monthly_invoice_limit, monthly_upload_limit). 0 = unlimited.
#
# The four business roles A1.5 made directly assignable (finance_manager/
# accountant/approver/auditor) are professional/business roles, not a
# free/paid-tier distinction — they default unlimited, same as admin/owner.
# Without an entry here, `_get_or_seed` would `KeyError` the first time a
# member holding one of these roles created an invoice or uploaded a document
# (both call `enforce_*_quota(db, org_id, current.role)` with the caller's raw
# stored role) — a real latent defect this order fixes, not just a widening.
LIMIT_DEFAULTS: dict[UserRole, tuple[int, int]] = {
    UserRole.user_free: (10, 20),
    UserRole.user: (1000, 2000),
    UserRole.admin: (0, 0),
    UserRole.owner: (0, 0),
    UserRole.finance_manager: (0, 0),
    UserRole.accountant: (0, 0),
    UserRole.approver: (0, 0),
    UserRole.auditor: (0, 0),
}

# Display metadata for the matrix UI.
ROLE_META: dict[UserRole, dict] = {
    UserRole.user_free: {
        "label": "User-free",
        "paid": False,
        "desc": "Non-paying user — limited access.",
    },
    UserRole.user: {"label": "User", "paid": True, "desc": "Paying user."},
    UserRole.admin: {
        "label": "Admin",
        "paid": True,
        "desc": "Business administration within the company.",
    },
    UserRole.owner: {
        "label": "Owner",
        "paid": True,
        "desc": "The company's primary user — full administration of their company.",
    },
    UserRole.finance_manager: {
        "label": "Finance Manager",
        "paid": True,
        "desc": "Runs the finance function — full money surfaces + approve, send, export, audit-read.",
    },
    UserRole.accountant: {
        "label": "Accountant",
        "paid": True,
        "desc": "Books the numbers — invoices/expenses/issuing + cash application + export.",
    },
    UserRole.approver: {
        "label": "Approver",
        "paid": True,
        "desc": "Approves expenses and invoices; otherwise read-only.",
    },
    UserRole.auditor: {
        "label": "Auditor",
        "paid": True,
        "desc": "Read-everything for assurance + the audit log + export.",
    },
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
        out.append(
            {
                "role": role.value,
                "label": meta["label"],
                "paid": meta["paid"],
                "description": meta["desc"],
                "monthly_invoice_limit": p.monthly_invoice_limit,
                "monthly_upload_limit": p.monthly_upload_limit,
            }
        )
    return out


async def set_limits(
    db: AsyncSession, role: UserRole, invoice_limit: int, upload_limit: int
) -> dict:
    p = await _get_or_seed(db, role)
    p.monthly_invoice_limit = max(0, int(invoice_limit))
    p.monthly_upload_limit = max(0, int(upload_limit))
    await db.commit()
    await db.refresh(p)
    meta = ROLE_META[role]
    return {
        "role": role.value,
        "label": meta["label"],
        "paid": meta["paid"],
        "description": meta["desc"],
        "monthly_invoice_limit": p.monthly_invoice_limit,
        "monthly_upload_limit": p.monthly_upload_limit,
    }


def _month_start() -> datetime:
    today = date.today()
    return datetime(today.year, today.month, 1, tzinfo=UTC)


async def invoice_limit_for(db: AsyncSession, role: UserRole) -> int:
    return (await _get_or_seed(db, role)).monthly_invoice_limit


async def invoices_this_month(db: AsyncSession, org_id: str) -> int:
    return (
        await db.scalar(
            select(func.count(Invoice.id)).where(
                Invoice.org_id == org_id, Invoice.created_at >= _month_start()
            )
        )
        or 0
    )


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


# --- Metered usage (uploads) ----------------------------------------------------


def _period() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


async def usage_count(db: AsyncSession, org_id: str, metric: str) -> int:
    row = await db.scalar(
        select(UsageCounter.count).where(
            UsageCounter.org_id == org_id,
            UsageCounter.period == _period(),
            UsageCounter.metric == metric,
        )
    )
    return int(row or 0)


async def record_usage(db: AsyncSession, org_id: str, metric: str, n: int = 1) -> None:
    """Increment this month's counter for `metric` (create the row if needed)."""
    counter = await db.scalar(
        select(UsageCounter).where(
            UsageCounter.org_id == org_id,
            UsageCounter.period == _period(),
            UsageCounter.metric == metric,
        )
    )
    if counter is None:
        counter = UsageCounter(org_id=org_id, period=_period(), metric=metric, count=0)
        db.add(counter)
        try:
            await db.flush()
        except IntegrityError:  # concurrent create — reload the winner
            await db.rollback()
            counter = await db.scalar(
                select(UsageCounter).where(
                    UsageCounter.org_id == org_id,
                    UsageCounter.period == _period(),
                    UsageCounter.metric == metric,
                )
            )
    counter.count = (counter.count or 0) + n
    await db.commit()


async def upload_limit_for(db: AsyncSession, role: UserRole) -> int:
    return (await _get_or_seed(db, role)).monthly_upload_limit


async def enforce_upload_quota(db: AsyncSession, org_id: str, role: UserRole) -> None:
    """Raise 402 if the acting role's monthly upload limit is already reached."""
    limit = await upload_limit_for(db, role)
    if limit <= 0:
        return  # unlimited
    used = await usage_count(db, org_id, "upload")
    if used >= limit:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Monthly upload limit reached ({used}/{limit}) for your access level. "
            "Upgrade your plan or ask an admin to raise the limit.",
        )


async def usage(db: AsyncSession, org_id: str, role: UserRole) -> dict:
    limit = await invoice_limit_for(db, role)
    used = await invoices_this_month(db, org_id)
    up_limit = await upload_limit_for(db, role)
    up_used = await usage_count(db, org_id, "upload")
    return {
        "role": role.value,
        "invoices_used": used,
        "invoice_limit": limit,
        "invoices_remaining": (max(0, limit - used) if limit > 0 else None),
        "unlimited": limit <= 0,
        "uploads_used": up_used,
        "upload_limit": up_limit,
        "uploads_remaining": (max(0, up_limit - up_used) if up_limit > 0 else None),
    }
