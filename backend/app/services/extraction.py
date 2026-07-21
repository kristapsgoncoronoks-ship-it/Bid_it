"""Extraction-lineage service (Slice 5b).

Records one row per capture attempt at the parse choke point and links it to the
invoice when the reviewed draft is saved. Tenant-scoped by the caller's `org_id`.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction_field import ExtractionField
from app.models.extraction_run import ExtractionRun


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def record_fields(db: AsyncSession, org_id: str, run_id: str, fields) -> None:
    """Persist per-field provenance for a run (Slice 5f). `fields` are the parser's
    FieldProvenance items. Commits (the run already exists)."""
    for f in fields:
        db.add(
            ExtractionField(
                org_id=org_id,
                extraction_run_id=run_id,
                field=f.field,
                value=(f.value[:500] if f.value else None),
                status=f.status,
                confidence=f.confidence,
            )
        )
    if fields:
        await db.commit()


async def fields_for_run(db: AsyncSession, org_id: str, run_id: str) -> list[ExtractionField]:
    return list(
        await db.scalars(
            select(ExtractionField)
            .where(ExtractionField.org_id == org_id, ExtractionField.extraction_run_id == run_id)
            .order_by(ExtractionField.field.asc())
        )
    )


async def record(
    db: AsyncSession,
    org_id: str,
    *,
    filename: str | None,
    sha256: str | None,
    method: str,
    status: str,
    field_count: int = 0,
    warning_count: int = 0,
    note: str | None = None,
) -> ExtractionRun:
    """Persist a capture attempt (no invoice yet). Commits."""
    run = ExtractionRun(
        org_id=org_id,
        source_filename=filename,
        source_sha256=sha256,
        method=method,
        status=status,
        field_count=field_count,
        warning_count=warning_count,
        note=(note[:2000] if note else None),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def link_to_invoice(db: AsyncSession, org_id: str, run_id: str, invoice_id: str) -> bool:
    """Attach a still-unlinked capture run to the saved invoice (status→saved).
    Tenant-scoped; only an unlinked run in this org is touched. Does NOT commit —
    the caller commits with the invoice save. Returns whether a row was linked."""
    result = await db.execute(
        update(ExtractionRun)
        .where(
            ExtractionRun.id == run_id,
            ExtractionRun.org_id == org_id,
            ExtractionRun.invoice_id.is_(None),
        )
        .values(invoice_id=invoice_id, status="saved")
    )
    return result.rowcount > 0


async def list_for_invoice(db: AsyncSession, org_id: str, invoice_id: str) -> list[ExtractionRun]:
    return list(
        await db.scalars(
            select(ExtractionRun)
            .where(ExtractionRun.org_id == org_id, ExtractionRun.invoice_id == invoice_id)
            .order_by(ExtractionRun.created_at.asc())
        )
    )
