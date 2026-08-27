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

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenant
from app.models.expense import ExpenseReport, ExpenseTransaction
from app.models.issued_invoice import IssuedInvoiceAttachment
from app.models.recurring_invoice import RecurringInvoice
from app.services import documents
from app.services.invoices import BIN_RETENTION_DAYS

log = logging.getLogger(__name__)


class BinError(Exception):
    """Unknown kind / unknown id — the route maps to 404."""


@dataclass(frozen=True)
class Kind:
    model: type
    label: str
    snapshot: Callable[[Any], dict]
    #: WO-V — object-storage bytes this kind owns, destroyed AT PURGE and never
    #: before. Returns `(document-class prefix, sha256)` pairs for a batch of
    #: rows about to be destroyed.
    #:
    #: The timing is the whole point. Binning is reversible for 30 days, so
    #: soft-delete must NOT touch the bytes — a restored expense report with no
    #: receipts is a restore that did not restore anything. Purge is the
    #: irreversible step, and that is where the files go.
    #:
    #: `None` means the kind owns no bytes (a recurring schedule, an inbox
    #: transaction). It is an explicit field rather than an optional convention
    #: so that adding a byte-bearing kind forces an answer — the alternative is
    #: a kind that silently orphans its files, which is exactly the defect this
    #: field exists to have prevented.
    bytes_of: Callable[[AsyncSession, str, list[str]], Awaitable[list[tuple[str, str]]]] | None = (
        None
    )


async def _expense_report_bytes(
    db: AsyncSession, org_id: str, ids: list[str]
) -> list[tuple[str, str]]:
    """Every receipt sha behind a batch of expense reports."""
    from app.models.expense import ExpenseItem

    shas = await db.scalars(
        select(ExpenseItem.receipt_sha256).where(
            ExpenseItem.report_id.in_(ids), ExpenseItem.receipt_sha256.is_not(None)
        )
    )
    return [(documents.RECEIPTS, sha) for sha in set(shas) if sha]


KINDS: dict[str, Kind] = {
    "expense_report": Kind(
        ExpenseReport,
        "Expense report",
        lambda r: {"title": r.title, "employee": r.employee_name, "status": r.status},
        bytes_of=_expense_report_bytes,
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
    """Destroy every generic-bin row past its window — AND the object-storage
    bytes it owned. Returns the audit meta: WHAT was destroyed, not just how
    many (until the platform archive covers these kinds, the audit event is the
    only remaining trace).

    WO-V ADDED THE BYTES. Until then this destroyed rows only, so an expense
    report purged from the bin left its receipt files behind forever — an
    orphan nobody could reach through the product and nobody knew to delete.
    That also made the bin UNSAFE as a destination for the retention purge,
    which does destroy bytes on its direct path: routing a byte-bearing
    category through a bin that dropped them would have been a regression
    wearing the shape of an improvement.

    Bytes go here and NOT at soft-delete time, because binning is reversible
    for `BIN_RETENTION_DAYS` and a restored report with no receipts has not
    been restored. Purge is the irreversible step; this is where files die.

    Deletion is best-effort and deliberately does not block the purge: object
    storage is a separate system that can be briefly unavailable, and a row
    that outlives its window because a bucket was down is a worse outcome than
    a file that outlives its row. A failure is logged, and the row still goes.
    """
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
            # Bytes BEFORE rows: the shas are reachable only through the rows
            # that are about to disappear.
            if kind.bytes_of is not None:
                try:
                    for prefix, sha in await kind.bytes_of(db, org_id, ids):
                        await documents.delete(prefix, org_id, sha)
                except Exception as exc:  # noqa: BLE001 - see the docstring
                    log.warning("bin: byte cleanup failed for %s/%s: %s", org_id, kind_key, exc)
            await db.execute(
                delete(kind.model).where(
                    kind.model.org_id == org_id,  # type: ignore[attr-defined]
                    kind.model.id.in_(ids),  # type: ignore[attr-defined]
                )
            )
    return {"purged": len(purged), "records": purged}
