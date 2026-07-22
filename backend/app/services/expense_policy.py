"""Expense spending policy (Phase 09).

One advisory policy per org. `violations()` flags out-of-policy line items so the
approver sees them at review time — it never blocks submission (the business-
purpose + receipt compliance gate stays the hard submit gate). Any cap left unset
is not enforced.
"""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import ExpenseItem, ExpensePolicy


async def get(db: AsyncSession, org_id: str) -> ExpensePolicy | None:
    return await db.scalar(select(ExpensePolicy).where(ExpensePolicy.org_id == org_id))


def caps_of(policy: ExpensePolicy | None) -> dict[str, Decimal]:
    if policy is None or not policy.category_caps:
        return {}
    try:
        raw = json.loads(policy.category_caps)
        return {str(k): Decimal(str(v)) for k, v in raw.items()}
    except (ValueError, TypeError, AttributeError):
        return {}


async def upsert(
    db: AsyncSession,
    org_id: str,
    *,
    active: bool = True,
    max_item_amount: Decimal | None = None,
    receipt_required_over: Decimal | None = None,
    category_caps: dict[str, str | Decimal] | None = None,
) -> ExpensePolicy:
    """Create or replace the org's policy. Flushes; caller commits."""
    policy = await get(db, org_id)
    caps_json = json.dumps({k: str(v) for k, v in category_caps.items()}) if category_caps else None
    if policy is None:
        policy = ExpensePolicy(org_id=org_id)
        db.add(policy)
    policy.active = active
    policy.max_item_amount = max_item_amount
    policy.receipt_required_over = receipt_required_over
    policy.category_caps = caps_json
    policy.version = (policy.version or 0) + 1  # None on a not-yet-flushed new row
    await db.flush()
    return policy


def violations(policy: ExpensePolicy | None, items: list[ExpenseItem]) -> list[dict]:
    """Per-item out-of-policy findings. Empty when there is no active policy."""
    if policy is None or not policy.active:
        return []
    caps = caps_of(policy)
    out: list[dict] = []
    for it in items:
        amount = Decimal(it.amount or 0)
        if policy.max_item_amount is not None and amount > Decimal(policy.max_item_amount):
            out.append(
                {
                    "item_id": it.id,
                    "category": it.category,
                    "code": "over_item_max",
                    "message": f"{amount} exceeds the per-item limit of {policy.max_item_amount}",
                    "amount": str(amount),
                    "limit": str(policy.max_item_amount),
                }
            )
        cap = caps.get(it.category)
        if cap is not None and amount > cap:
            out.append(
                {
                    "item_id": it.id,
                    "category": it.category,
                    "code": "over_category_cap",
                    "message": f"{amount} exceeds the {it.category} cap of {cap}",
                    "amount": str(amount),
                    "limit": str(cap),
                }
            )
        if (
            policy.receipt_required_over is not None
            and amount > Decimal(policy.receipt_required_over)
            and not it.receipt_sha256
        ):
            out.append(
                {
                    "item_id": it.id,
                    "category": it.category,
                    "code": "missing_receipt",
                    "message": f"a receipt is required for items over {policy.receipt_required_over}",
                    "amount": str(amount),
                    "limit": str(policy.receipt_required_over),
                }
            )
    return out
