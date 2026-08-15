"""The system matrix — per-PLAN usage limits + quota enforcement.

WO-47: this used to key off the ACTING USER'S ROLE (`RolePolicy`/`UserRole`),
which is wrong for a hybrid seats+usage commercial model — two members of the
SAME paying org saw different caps (a `user_free` teammate blocked while an
`admin` teammate in the identical org sailed past, unlimited, purely because
of an internal permission role that has nothing to do with what the ORG pays
for), and usage is metered ORG-WIDE (`invoices_this_month`/`usage_count` never
filtered by user) while the limit lookup was PER-CALLER. The cap now keys off
the same dimension usage is counted in: the org's subscription `plan`
(`app.services.plans.PLANS`). Every member of an org, regardless of role,
shares one org-level cap — see `plans.Plan.monthly_invoice_limit` /
`monthly_upload_limit` for the per-plan defaults and their provenance.

Enforcement gates **invoice creation** (counted off the invoices table) and
**document uploads** (metered in `usage_counters`, since a parse has no fact
row of its own). Both compare the org's plan's monthly limit to the tenant's
current-month usage — the guardrail already stated in the roadmap holds:
**never lose a document because of a limit**. The chosen overage policy is
BLOCK-AT-THE-CAP (the other option, auto-charge/metered overage billing, needs
a LIVE billing provider — `docs/DECISIONS-NEEDED.md` §2 — so it is not
available yet): every quota check in this module runs BEFORE anything is
persisted or the uploaded bytes are stored, so a block never discards data
that was ever accepted — the caller gets an explicit 402 and their file/draft
is untouched on their end, free to retry after the org upgrades or a sysadmin
raises the plan's cap. Nothing already stored is ever deleted or silently
dropped because of a limit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenant
from app.models.invoice import Invoice
from app.models.plan_policy import PlanPolicy
from app.models.usage import UsageCounter
from app.services import plans as plans_service

# Display metadata for the matrix UI, one row per plan key (insertion order of
# `plans.PLANS`: trial, starter, pro, enterprise).
_PLAN_META = {
    key: {
        "label": p.name,
        "paid": not p.trial,  # only the trial plan is free; enterprise (custom price) is still paid
        "description": (
            f"{p.name} plan — {p.seats} seat"
            + ("s" if p.seats != 1 else "")
            + (
                f", €{p.price_eur}/mo"
                if p.price_eur
                else (" (free trial)" if p.trial else " (custom pricing)")
            )
        ),
    }
    for key, p in plans_service.PLANS.items()
}


async def _get_or_seed(db: AsyncSession, plan_key: str) -> PlanPolicy:
    row = await db.scalar(select(PlanPolicy).where(PlanPolicy.plan == plan_key))
    if row is None:
        plan = plans_service.plan_for(plan_key)
        row = PlanPolicy(
            plan=plan.key,
            monthly_invoice_limit=plan.monthly_invoice_limit,
            monthly_upload_limit=plan.monthly_upload_limit,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def matrix(db: AsyncSession) -> list[dict]:
    """The full system matrix — one entry per PLAN (seeded on first read)."""
    out = []
    for key in plans_service.PLANS:
        p = await _get_or_seed(db, key)
        meta = _PLAN_META[key]
        out.append(
            {
                "plan": key,
                "label": meta["label"],
                "paid": meta["paid"],
                "description": meta["description"],
                "monthly_invoice_limit": p.monthly_invoice_limit,
                "monthly_upload_limit": p.monthly_upload_limit,
            }
        )
    return out


async def set_limits(
    db: AsyncSession, plan_key: str, invoice_limit: int, upload_limit: int
) -> dict:
    p = await _get_or_seed(db, plan_key)
    p.monthly_invoice_limit = max(0, int(invoice_limit))
    p.monthly_upload_limit = max(0, int(upload_limit))
    await db.commit()
    await db.refresh(p)
    meta = _PLAN_META[plan_key]
    return {
        "plan": plan_key,
        "label": meta["label"],
        "paid": meta["paid"],
        "description": meta["description"],
        "monthly_invoice_limit": p.monthly_invoice_limit,
        "monthly_upload_limit": p.monthly_upload_limit,
    }


def _month_start() -> datetime:
    today = date.today()
    return datetime(today.year, today.month, 1, tzinfo=UTC)


async def invoice_limit_for(db: AsyncSession, plan_key: str) -> int:
    return (await _get_or_seed(db, plan_key)).monthly_invoice_limit


async def invoices_this_month(db: AsyncSession, org_id: str) -> int:
    """Invoices CREATED this month — counted including binned ones.

    `include_deleted()` is not an oversight here, it is the point. A quota meters
    what the tenant created, not what is currently visible, and the recycle bin
    made those two different things. Without it: hit the cap, delete N invoices
    (reversible), create N more, then restore the first N from the bin inside 30
    days — a plan limit bypassed for free and repeatable every cycle. Hard delete
    allowed the same reset but destroyed the data, so it was self-punishing; the
    bin removed the punishment and left the loophole.
    """
    with tenant.include_deleted():
        return (
            await db.scalar(
                select(func.count(Invoice.id)).where(
                    Invoice.org_id == org_id, Invoice.created_at >= _month_start()
                )
            )
            or 0
        )


async def enforce_invoice_quota(db: AsyncSession, org_id: str, plan_key: str) -> None:
    """Raise 402 if the ORG's plan monthly invoice limit is reached.

    Runs before the invoice is persisted (see every call site) — a block never
    discards a document, it just refuses to create a new one; nothing that
    already exists is touched. See module docstring for the overage-policy
    rationale (block, not auto-charge, pending a live billing provider)."""
    limit = await invoice_limit_for(db, plan_key)
    if limit <= 0:
        return  # unlimited
    used = await invoices_this_month(db, org_id)
    if used >= limit:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Monthly invoice limit reached ({used}/{limit}) for your organization's plan. "
            "Upgrade your plan or ask a platform operator to raise the limit.",
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


async def upload_limit_for(db: AsyncSession, plan_key: str) -> int:
    return (await _get_or_seed(db, plan_key)).monthly_upload_limit


async def enforce_upload_quota(db: AsyncSession, org_id: str, plan_key: str) -> None:
    """Raise 402 if the ORG's plan monthly upload limit is already reached.

    Runs BEFORE the uploaded bytes are read/stored/queued (see the call site in
    `invoices.upload_invoice`) — nothing is ever accepted then discarded; the
    file simply isn't admitted yet. See module docstring."""
    limit = await upload_limit_for(db, plan_key)
    if limit <= 0:
        return  # unlimited
    used = await usage_count(db, org_id, "upload")
    if used >= limit:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Monthly upload limit reached ({used}/{limit}) for your organization's plan. "
            "Upgrade your plan or ask a platform operator to raise the limit.",
        )


async def usage(db: AsyncSession, org_id: str, plan_key: str) -> dict:
    limit = await invoice_limit_for(db, plan_key)
    used = await invoices_this_month(db, org_id)
    up_limit = await upload_limit_for(db, plan_key)
    up_used = await usage_count(db, org_id, "upload")
    return {
        "plan": plan_key,
        "invoices_used": used,
        "invoice_limit": limit,
        "invoices_remaining": (max(0, limit - used) if limit > 0 else None),
        "unlimited": limit <= 0,
        "uploads_used": up_used,
        "upload_limit": up_limit,
        "uploads_remaining": (max(0, up_limit - up_used) if up_limit > 0 else None),
    }
