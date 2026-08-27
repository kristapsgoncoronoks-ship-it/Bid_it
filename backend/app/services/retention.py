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
from typing import Any

from sqlalchemy import delete, func, select, update
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
    model: Any
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


#: Retention categories that SOFT-delete into a recycle bin instead of
#: destroying rows outright. Membership is not a preference — a category may
#: only be listed here once a bin exists that can actually hold its rows AND
#: destroy its bytes at purge. `email_intake` is absent because `InboundInvoice`
#: has no `deleted_at` column at all: routing it would be a hard delete wearing
#: a bin's name. See `purge_expired`'s docstring.
_BINNED_CATEGORIES = frozenset({"invoices", "expenses"})


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


RETENTION_ACTOR = "retention policy"
"""What `Invoice.deleted_by` says when the retention policy, not a person,
moved the invoice to the bin. A Trash-screen reader deciding whether to restore
needs to know the difference between a colleague's click and the org's own
configured policy — and the policy will take the record again on its next run."""


async def purge(db: AsyncSession, org_id: str, *, today: date | None = None) -> dict:
    """Enforce each configured retention policy — through ONE destruction path.

    Refuses (no-op) while a legal hold is active. Returns
    `{"held": bool, "purged": {category: count}}`. Audited when anything is
    purged. Never raises on a business outcome.

    INVOICES ROUTE THROUGH THE DELETION CHAIN (owner decision 2026-08-16,
    resolving P0-2 of docs/audit/2026-08-16-bug-scan.md). This function used to
    hard-delete invoices directly — no recycle bin, no archive copy — which made
    it a second destruction path with strictly weaker guarantees than the one
    clients see. Now it SOFT-DELETES them into the bin, exactly as a person's
    delete does, and the ordinary chain takes over: 30 days restorable, then the
    bin purge archives and destroys, then the archive holds its three years.
    Consequences that fall out of that, all intended:

      - a policy mistake is recoverable for 30 days instead of instantly fatal;
      - the archive copy is made by the SAME code path as every other deletion,
        so it cannot drift from it;
      - rows already in the bin are naturally invisible to `_purgeable_ids`
        (the central soft-delete guard hides them) and are simply left to the
        bin's own clock — the old code could not see them either, but destroyed
        nothing in their place, leaving them to outlive the policy unnoticed;
      - a record restored from the bin whose age still exceeds the window is
        re-binned on the next daily run. That is the policy working, and
        `deleted_by` says so on the Trash screen;
      - the archive's own three years then run REGARDLESS of the tenant's
        shorter policy (owner decision, same date): the archive is the
        platform's compliance backstop and deliberately outlives client-side
        deletion. That sentence belongs in the DPA, not in a surprise.

    No per-record consent gate applies here: configuring the policy IS the
    standing consent, given once by an administrator instead of per click.

    EXPENSES ROUTE THROUGH THE BIN TOO (WO-V). This docstring used to say they
    kept the direct hard-delete "UNTIL the recycle bin learns those entities" —
    and the bin had learned `expense_report` in WO-M itself, so the sentence
    outlived its own condition and the category kept hard-deleting for an arc.
    WO-V routes it, but only after fixing what made routing unsafe: the generic
    bin's purge destroyed ROWS and not BYTES, so sending a category with
    receipts through it would have silently orphaned every file. `bin.Kind`
    now carries a `bytes_of` hook and `bin.purge_expired` uses it.

    INBOUND EMAIL ATTACHMENTS still hard-delete, and that one is genuine: the
    bin cannot hold `InboundInvoice` at all — the model has no `deleted_at`, so
    there is nothing to stamp. Giving it the columns is a migration plus a
    `KINDS` entry plus `SOFT_DELETE_MODELS` registration, tracked as its own
    work rather than smuggled in here. The rule the WO-V note above records:
    never route a category into a bin that cannot hold it, because that is not
    a recycle bin — it is a differently-spelled hard delete.
    """
    today = today or date.today()
    if await is_on_hold(db, org_id):
        return {"held": True, "purged": {}}

    stamp = datetime.now(UTC)
    policies = await get_policies(db, org_id)
    purged: dict[str, int] = {}
    for key, retain_days in policies.items():
        cat = CATEGORIES[key]
        ids = await _purgeable_ids(db, org_id, cat, _cutoff(today, retain_days))
        if not ids:
            continue
        if key in _BINNED_CATEGORIES:
            model = cat.model
            # Batched: an unbounded IN hits the bind-parameter ceiling (65535
            # Postgres / 32766 SQLite) and a daily job that fails on size fails
            # forever — the same trap the bin purge documents.
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                await db.execute(
                    update(model)
                    .where(
                        model.org_id == org_id,
                        model.id.in_(chunk),
                        # Re-asserted on the UPDATE: the tenant guard skips
                        # non-SELECT statements, and racing a manual delete must
                        # not overwrite who/when the bin already recorded.
                        model.deleted_at.is_(None),
                    )
                    .values(deleted_at=stamp, deleted_by=RETENTION_ACTOR)
                )
            purged[key] = len(ids)
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
