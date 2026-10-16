"""R014: complete-or-refused PDF archive with an exact bounded manifest."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import UTC, date, datetime
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError, NotFoundError, ValidationError
from app.models.issued_invoice import IssuedInvoice
from app.services import filesec, issued_status

MAX_DOCUMENTS = 500


async def build(
    db: AsyncSession,
    org_id: str,
    *,
    date_from: date | None,
    date_to: date | None,
    status_filter: str | None,
    render,
) -> tuple[bytes, str]:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValidationError("ZIP period start must not follow its end.")
    if status_filter and status_filter not in issued_status.STATUS_LABELS:
        raise ValidationError("Unknown ZIP invoice status.")
    now = datetime.now(UTC)
    today = date.today()
    stmt = (
        select(IssuedInvoice)
        .where(
            IssuedInvoice.org_id == org_id,
            IssuedInvoice.number.is_not(None),
            IssuedInvoice.lifecycle.not_in(("draft", "approved")),
        )
        .options(selectinload(IssuedInvoice.lines))
        .order_by(IssuedInvoice.issue_date, IssuedInvoice.id)
    )
    if date_from is not None:
        stmt = stmt.where(IssuedInvoice.issue_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(IssuedInvoice.issue_date <= date_to)
    if status_filter:
        stmt = stmt.where(issued_status.sql_status(today) == status_filter)
    invoices = list(await db.scalars(stmt.limit(MAX_DOCUMENTS + 1)))
    if len(invoices) > MAX_DOCUMENTS:
        raise ValidationError(
            f"More than {MAX_DOCUMENTS} numbered invoices match. Narrow the ZIP period or status; no partial archive was created.",
            code="export_too_many",
        )
    if not invoices:
        raise NotFoundError("No numbered invoices match the ZIP period and status.")
    limit = filesec.RESOURCE_BYTES["invoice_zip"]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "complete": True,
        "count": len(invoices),
        "selection_captured_at": now.isoformat(),
        "status_as_of": today.isoformat(),
        "scope": {
            "org_id": org_id,
            "from": str(date_from) if date_from else None,
            "to": str(date_to) if date_to else None,
            "status": status_filter or None,
            "numbered_only": True,
            "issuers": "all within this workspace",
        },
        "limits": {"documents": MAX_DOCUMENTS, "bytes": limit},
        "documents": [],
    }
    buffer = io.BytesIO()
    source_bytes = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for inv in invoices:
            try:
                pdf = await render(inv)
            except AppError:
                raise
            except Exception as exc:
                raise AppError(
                    "A selected invoice PDF is unavailable; no partial archive was returned.",
                    code="export_source_unavailable",
                    status=503,
                ) from exc
            if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF"):
                raise AppError(
                    "A selected invoice PDF could not be produced; no partial ZIP was returned.",
                    code="export_source_unavailable",
                    status=503,
                )
            source_bytes += len(pdf)
            if source_bytes > limit:
                raise ValidationError(
                    "The ZIP exceeds its document byte budget. Narrow the period or status; no partial archive was returned.",
                    code="export_too_large",
                )
            stem = re.sub(r"[^A-Za-z0-9_.-]", "_", inv.number or "invoice")[:80]
            filename = f"invoice-{stem}-{inv.id}.pdf"
            await run_in_threadpool(archive.writestr, filename, pdf)
            manifest["documents"].append(
                {
                    "id": inv.id,
                    "issuer_id": inv.issuer_id,
                    "number": inv.number,
                    "record_updated_at": inv.updated_at.isoformat(),
                    "currency": inv.currency,
                    "issue_date": inv.issue_date.isoformat(),
                    "status": issued_status.status_of(inv, today),
                    "filename": filename,
                    "bytes": len(pdf),
                    "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
                }
            )
        await run_in_threadpool(
            archive.writestr, "manifest.json", json.dumps(manifest, indent=2).encode()
        )
    if buffer.tell() > limit:
        raise ValidationError(
            "The ZIP exceeds its output byte budget. Narrow the period or status; no partial archive was returned.",
            code="export_too_large",
        )
    return buffer.getvalue(), f"invoices_{date_from or 'all'}_{date_to or 'all'}.zip"
