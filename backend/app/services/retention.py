"""Data retention + legal hold (Phase 4, ADR-0019).

Retention automatically purges tenant records older than a configured window
(GDPR Art. 5(1)(e) storage limitation). A LEGAL HOLD suspends all purging while
active (the e-discovery preservation duty overrides data minimization). Both are
per-tenant, admin-controlled, audited, and safe by default (no policy = keep
forever).

Design:
- Purge is measured from a record's CREATION time (`created_at`) — a predictable,
  uniform basis across categories. Set windows conservatively for ledger data.
- Deletes are explicit (children first, then parents) so behaviour is identical
  on SQLite (tests) and Postgres regardless of FK-cascade enforcement, and
  associated object-storage bytes (receipts, email attachments) are removed too.
- Every purge run is audit-logged (`retention.purge`, per-category counts). The
  purge itself never deletes `audit_events` — the tamper-evident trail is the
  compliance record.
- `issued_invoices` is deliberately NOT purgeable: gap-free numbering + the
  audit snapshot make ledger deletion a separate, carefully-gated decision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_intake import InboundInvoice
from app.models.expense import ExpenseComment, ExpenseItem, ExpenseReport
from app.models.invoice import Invoice, LineItem
from app.models.retention import LegalHold, RetentionPolicy
from app.services import audit, documents

log = logging.getLogger("invoiceiq.retention")


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    model: type
    # (child_model, foreign-key column) pairs deleted before the parent.
    children: tuple = ()


CATEGORIES: dict[str, Category] = {
    "invoices": Category(
        "invoices",
        "Received invoices",
        Invoice,
        children=((LineItem, LineItem.invoice_id),),
    ),
    "expenses": Category(
        "expenses",
        "Expense reports & receipts",
        ExpenseReport,
        children=((ExpenseItem, ExpenseItem.report_id), (ExpenseComment, ExpenseComment.report_id)),
    ),
    "email_intake": Category(
        "email_intake",
        "Inbound email attachments",
        InboundInvoice,
    ),
}


def _cutoff(today: date, retain_days: int) -> datetime:
    """UTC-midnight boundary: records created strictly before this are purgeable."""
    return datetime.combine(today - timedelta(days=retain_days), time.min, tzinfo=UTC)


# --- policies --------------------------------------------------------------


async def get_policies(db: AsyncSession, org_id: str) -> dict[str, int]:
    rows = await db.scalars(select(RetentionPolicy).where(RetentionPolicy.org_id == org_id))
    return {r.category: r.retain_days for r in rows if r.category in CATEGORIES}


async def set_policy(db: AsyncSession, org_id: str, category: str, retain_days: int) -> None:
    """Upsert a policy; `retain_days <= 0` removes it (disables purging)."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown retention category: {category}")
    existing = await db.scalar(
        select(RetentionPolicy).where(
            RetentionPolicy.org_id == org_id, RetentionPolicy.category == category
        )
    )
    if retain_days <= 0:
        if existing is not None:
            await db.delete(existing)
    elif existing is not None:
        existing.retain_days = retain_days
    else:
        db.add(RetentionPolicy(org_id=org_id, category=category, retain_days=retain_days))
    await audit.record(
        db,
        "retention.policy_set",
        org_id=org_id,
        meta={"category": category, "retain_days": retain_days},
    )
    await db.commit()


# --- legal holds -----------------------------------------------------------


async def active_holds(db: AsyncSession, org_id: str) -> list[LegalHold]:
    return list(
        await db.scalars(
            select(LegalHold)
            .where(LegalHold.org_id == org_id, LegalHold.active.is_(True))
            .order_by(LegalHold.created_at.desc())
        )
    )


async def is_on_hold(db: AsyncSession, org_id: str) -> bool:
    found = await db.scalar(
        select(LegalHold.id).where(LegalHold.org_id == org_id, LegalHold.active.is_(True)).limit(1)
    )
    return found is not None


async def place_hold(
    db: AsyncSession, org_id: str, *, reason: str, actor_email: str | None
) -> LegalHold:
    hold = LegalHold(org_id=org_id, reason=reason, active=True, placed_by=actor_email)
    db.add(hold)
    await db.flush()
    await audit.record(
        db,
        "retention.hold_placed",
        org_id=org_id,
        target_type="legal_hold",
        target_id=hold.id,
        meta={"reason": reason},
    )
    await db.commit()
    await db.refresh(hold)
    return hold


async def release_hold(
    db: AsyncSession, org_id: str, hold_id: str, *, actor_email: str | None
) -> bool:
    hold = await db.scalar(
        select(LegalHold).where(LegalHold.org_id == org_id, LegalHold.id == hold_id)
    )
    if hold is None or not hold.active:
        return False
    hold.active = False
    hold.released_by = actor_email
    hold.released_at = datetime.now(UTC)
    await audit.record(
        db, "retention.hold_released", org_id=org_id, target_type="legal_hold", target_id=hold.id
    )
    await db.commit()
    return True


# --- preview + purge -------------------------------------------------------


async def _purgeable_ids(
    db: AsyncSession, org_id: str, cat: Category, cutoff: datetime
) -> list[str]:
    return list(
        await db.scalars(
            select(cat.model.id).where(cat.model.org_id == org_id, cat.model.created_at < cutoff)
        )
    )


@dataclass
class PurgePreview:
    on_hold: bool
    counts: dict[str, int] = field(default_factory=dict)


async def preview(db: AsyncSession, org_id: str, *, today: date | None = None) -> PurgePreview:
    """How many records EACH policy would purge today (independent of the hold),
    plus whether a hold is currently blocking the purge."""
    today = today or date.today()
    policies = await get_policies(db, org_id)
    counts: dict[str, int] = {}
    for key, retain_days in policies.items():
        cat = CATEGORIES[key]
        n = await db.scalar(
            select(func.count())
            .select_from(cat.model)
            .where(cat.model.org_id == org_id, cat.model.created_at < _cutoff(today, retain_days))
        )
        counts[key] = int(n or 0)
    return PurgePreview(on_hold=await is_on_hold(db, org_id), counts=counts)


async def _delete_object_bytes(
    org_id: str, cat: Category, db: AsyncSession, ids: list[str]
) -> None:
    """Best-effort removal of object-storage bytes tied to the purged rows."""
    try:
        if cat.key == "expenses":
            shas = await db.scalars(
                select(ExpenseItem.receipt_sha256).where(
                    ExpenseItem.report_id.in_(ids), ExpenseItem.receipt_sha256.is_not(None)
                )
            )
            for sha in set(shas):
                await documents.delete(documents.RECEIPTS, org_id, sha)
        elif cat.key == "email_intake":
            shas = await db.scalars(
                select(InboundInvoice.sha256).where(
                    InboundInvoice.id.in_(ids), InboundInvoice.sha256.is_not(None)
                )
            )
            for sha in set(shas):
                await documents.delete(documents.EMAIL_ATTACHMENTS, org_id, sha)
    except Exception as exc:  # noqa: BLE001 - bytes cleanup must not block the purge
        log.warning("object cleanup failed for %s/%s: %s", org_id, cat.key, exc)


async def purge(db: AsyncSession, org_id: str, *, today: date | None = None) -> dict:
    """Delete records past their retention window for every configured category.

    Refuses (no-op) while a legal hold is active. Returns
    `{"held": bool, "purged": {category: count}}`. Audited when anything is
    purged. Never raises on a business outcome."""
    today = today or date.today()
    if await is_on_hold(db, org_id):
        return {"held": True, "purged": {}}

    policies = await get_policies(db, org_id)
    purged: dict[str, int] = {}
    for key, retain_days in policies.items():
        cat = CATEGORIES[key]
        ids = await _purgeable_ids(db, org_id, cat, _cutoff(today, retain_days))
        if not ids:
            continue
        await _delete_object_bytes(org_id, cat, db, ids)
        for child_model, fk in cat.children:
            await db.execute(delete(child_model).where(fk.in_(ids)))
        await db.execute(delete(cat.model).where(cat.model.id.in_(ids)))
        purged[key] = len(ids)

    if purged:
        await audit.record(db, "retention.purge", org_id=org_id, meta={"purged": purged})
        await db.commit()
    return {"held": False, "purged": purged}


async def orgs_with_policy(db: AsyncSession) -> list[str]:
    """Distinct org ids that have at least one retention policy (UNSCOPED —
    scheduler context). Used to enqueue a purge only where it can do work."""
    return list(await db.scalars(select(RetentionPolicy.org_id).distinct()))
