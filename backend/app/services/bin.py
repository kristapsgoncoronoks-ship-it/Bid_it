"""The generic recycle bin (WO-M) — every entity the owner extended it to.

Owner decision 2026-08-15: *"bin extends to all entities."* Invoices got the
bin first (`invoices.delete_one` and friends, with their own consent
ceremony and archive interplay); this module is the SAME promise for the
entities that were still destroyed on click — expense reports, expense
inbox transactions, recurring schedules and issued-invoice attachments:

- deleting STAMPS `deleted_at`/`deleted_by` (the ORM guard's soft-delete
  criteria hides the row from every ordinary read);
- the Trash screen lists them with days left, and restore clears the stamp;
- the daily BIN_PURGE job destroys anything past `BIN_RETENTION_DAYS`,
  audited with what was destroyed (same reasoning as the invoice purge:
  a binned record must be recoverable, then GONE — never invisible and
  immortal).

The KINDS registry is the single source for "what is binnable here": each
entry names the model, the human label, and the snapshot the audit event
keeps. Adding an entity to the bin = one row here + the two columns +
SOFT_DELETE_MODELS registration (the guard test pins that agreement).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenant
from app.models.expense import ExpenseReport, ExpenseTransaction
from app.models.issued_invoice import IssuedInvoiceAttachment
from app.models.recurring_invoice import RecurringInvoice
from app.services.invoices import BIN_RETENTION_DAYS


class BinError(Exception):
    """Unknown kind / unknown id — the route maps to 404."""


@dataclass(frozen=True)
class Kind:
    model: type
    label: str
    snapshot: Callable[[Any], dict]


KINDS: dict[str, Kind] = {
    "expense_report": Kind(
        ExpenseReport,
        "Expense report",
        lambda r: {"title": r.title, "employee": r.employee_name, "status": r.status},
    ),
    "expense_transaction": Kind(
        ExpenseTransaction,
        "Expense inbox transaction",
        lambda r: {"description": r.description, "amount": str(r.amount)},
    ),
    "recurring_schedule": Kind(
        RecurringInvoice,
        "Recurring schedule",
        lambda r: {"title": r.title, "frequency": r.frequency},
    ),
    "issued_attachment": Kind(
        IssuedInvoiceAttachment,
        "Invoice attachment",
        lambda r: {"filename": r.filename, "invoice_id": r.invoice_id},
    ),
}


def stamp(row: Any, actor: str | None) -> None:
    """Move ONE loaded row into the bin — the only place the stamp is set for
    the generic kinds, mirroring `invoices._bin`."""
    row.deleted_at = datetime.now(UTC)
    row.deleted_by = actor


def _days_left(deleted_at: datetime, now: datetime) -> int:
    if deleted_at.tzinfo is None:  # SQLite hands back naive datetimes
        deleted_at = deleted_at.replace(tzinfo=UTC)
    gone = deleted_at + timedelta(days=BIN_RETENTION_DAYS)
    return max(0, (gone - now).days)


async def list_binned(db: AsyncSession, org_id: str) -> list[dict]:
    """Everything in the generic bin, newest first — the Trash screen's
    second table."""
    now = datetime.now(UTC)
    out: list[dict] = []
    with tenant.include_deleted():
        for kind_key, kind in KINDS.items():
            rows: Any = await db.scalars(
                select(kind.model).where(
                    kind.model.org_id == org_id,  # type: ignore[attr-defined]
                    kind.model.deleted_at.is_not(None),  # type: ignore[attr-defined]
                )
            )
            for r in rows:
                out.append(
                    {
                        "kind": kind_key,
                        "label": kind.label,
                        "id": r.id,
                        "summary": kind.snapshot(r),
                        "deleted_at": r.deleted_at.isoformat(),
                        "deleted_by": r.deleted_by,
                        "days_left": _days_left(r.deleted_at, now),
                    }
                )
    out.sort(key=lambda x: x["deleted_at"], reverse=True)
    return out


async def restore(db: AsyncSession, org_id: str, kind_key: str, row_id: str) -> dict:
    kind = KINDS.get(kind_key)
    if kind is None:
        raise BinError(f"'{kind_key}' is not a binnable kind")
    with tenant.include_deleted():
        row: Any = await db.scalar(
            select(kind.model).where(
                kind.model.org_id == org_id,  # type: ignore[attr-defined]
                kind.model.id == row_id,  # type: ignore[attr-defined]
                kind.model.deleted_at.is_not(None),  # type: ignore[attr-defined]
            )
        )
    if row is None:
        raise BinError("Nothing with that id is in the bin")
    row.deleted_at = None
    row.deleted_by = None
    await db.flush()
    return {"kind": kind_key, "id": row_id, "summary": kind.snapshot(row)}


async def purge_expired(db: AsyncSession, org_id: str, *, now: datetime | None = None) -> dict:
    """Destroy every generic-bin row past its window. Returns the audit meta —
    WHAT was destroyed, not just how many (until the platform archive covers
    these kinds, the audit event is the only remaining trace)."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=BIN_RETENTION_DAYS)
    purged: list[dict] = []
    with tenant.include_deleted():
        for kind_key, kind in KINDS.items():
            rows: Any = list(
                await db.scalars(
                    select(kind.model).where(
                        kind.model.org_id == org_id,  # type: ignore[attr-defined]
                        kind.model.deleted_at.is_not(None),  # type: ignore[attr-defined]
                        kind.model.deleted_at <= cutoff,  # type: ignore[attr-defined]
                    )
                )
            )
            if not rows:
                continue
            ids = [r.id for r in rows]
            purged.extend({"kind": kind_key, "id": r.id, "summary": kind.snapshot(r)} for r in rows)
            await db.execute(
                delete(kind.model).where(
                    kind.model.org_id == org_id,  # type: ignore[attr-defined]
                    kind.model.id.in_(ids),  # type: ignore[attr-defined]
                )
            )
    return {"purged": len(purged), "records": purged}
