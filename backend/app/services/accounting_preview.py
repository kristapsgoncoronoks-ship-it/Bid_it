"""A bounded, signed, immutable generic CSV preview; no importer acceptance claim.

The snapshot stays with the authorized browser and is sent only in POST bodies.
Purpose-derived HMAC keys prevent using this snapshot as an authentication token.
No new financial/close domain or durable archive is introduced.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
import zlib
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.invoice import Invoice, WorkflowState
from app.models.issuer import IssuerProfile
from app.schemas.accounting_export import AccountingPreview, AccountingPreviewRow, AccountingScope
from app.services import erp_export, filesec

CONTRACT = "generic-v2"
MAX_ROWS = 1000
MAX_BYTES = filesec.RESOURCE_BYTES["accounting_snapshot"]
LIFETIME_SECONDS = 600
APPROVED = (
    WorkflowState.approved,
    WorkflowState.scheduled_for_payment,
    WorkflowState.partially_paid,
    WorkflowState.paid,
)
PURPOSE = b"invoiceiq:accounting-preview:generic-v2"


def _key(snapshot_bytes: str) -> bytes:
    return hmac.digest(snapshot_bytes.encode(), PURPOSE, "sha256")


def _encode(snapshot_bytes: bytes) -> str:
    return base64.urlsafe_b64encode(snapshot_bytes).decode().rstrip("=")


def _decode(snapshot_bytes: str) -> bytes:
    return base64.b64decode(
        snapshot_bytes + "=" * (-len(snapshot_bytes) % 4), altchars=b"-_", validate=True
    )


async def _count(db: AsyncSession, filters: list) -> int:
    return int(await db.scalar(select(func.count()).select_from(Invoice).where(*filters)) or 0)


async def prepare(
    db: AsyncSession, org_id: str, user_id: str, scope: AccountingScope
) -> AccountingPreview:
    entity = await db.scalar(
        select(IssuerProfile).where(
            IssuerProfile.org_id == org_id, IssuerProfile.id == str(scope.entity_id)
        )
    )
    if entity is None:
        raise NotFoundError("Legal entity not found.")
    period = [
        Invoice.org_id == org_id,
        Invoice.deleted_at.is_(None),
        Invoice.issue_date >= scope.date_from,
        Invoice.issue_date <= scope.date_to,
    ]
    entity_filters = [*period, Invoice.legal_entity_id == str(scope.entity_id)]
    base = [*entity_filters, Invoice.currency == scope.currency]
    approved = [*base, Invoice.workflow_state.in_(APPROVED)]
    filters = [*approved]
    if scope.status is not None:
        filters.append(Invoice.status == scope.status)
    if scope.q:
        filters.append(Invoice.invoice_number.ilike(f"%{scope.q.strip()}%"))
    if scope.workflow_state:
        if scope.workflow_state == "in_approval":
            filters.append(
                Invoice.workflow_state.in_(
                    (WorkflowState.submitted, WorkflowState.partially_approved)
                )
            )
        else:
            try:
                filters.append(Invoice.workflow_state == WorkflowState(scope.workflow_state))
            except ValueError as exc:
                raise ValidationError("Unknown workflow-state filter.") from exc
    invoices = list(
        await db.scalars(
            select(Invoice)
            .where(*filters)
            .options(selectinload(Invoice.vendor))
            .order_by(Invoice.issue_date, Invoice.id)
            .limit(MAX_ROWS + 1)
        )
    )
    if len(invoices) > MAX_ROWS:
        raise ValidationError(
            f"This preview exceeds {MAX_ROWS} invoices. Narrow the entity, period or filters.",
            code="export_preview_too_large",
        )
    rows = [erp_export.row_of(invoice) for invoice in invoices]
    not_selected = Invoice.id.not_in([invoice.id for invoice in invoices])
    expires = int(time.time()) + LIFETIME_SECONDS
    snapshot_id = str(uuid.uuid4())
    net = sum((row.subtotal for row in rows), Decimal("0"))
    tax = sum((row.tax for row in rows), Decimal("0"))
    gross = sum((row.total for row in rows), Decimal("0"))
    missing_fx = sum(row.total_eur is None for row in rows)
    gross_eur = (
        None
        if missing_fx
        else str(sum((row.total_eur for row in rows if row.total_eur is not None), Decimal("0")))
    )
    digest, token = None, None
    if rows:
        filename, csv_text = erp_export.render("generic", rows)
        if len(csv_text.encode()) > MAX_BYTES:
            raise ValidationError(
                "The preview is too large. Narrow its scope.", code="export_preview_too_large"
            )
        digest = hashlib.sha256(csv_text.encode()).hexdigest()
        data = {
            "org_id": org_id,
            "user_id": user_id,
            "contract": CONTRACT,
            "snapshot_id": snapshot_id,
            "expires_at": expires,
            "filename": filename,
            "csv": csv_text,
            "sha256": digest,
            "scope": scope.model_dump(mode="json"),
            "invoice_versions": [[invoice.id, invoice.version] for invoice in invoices],
        }
        snapshot_bytes = json.dumps(data, separators=(",", ":")).encode()
        if len(snapshot_bytes) > MAX_BYTES:
            raise ValidationError(
                "The preview is too large. Narrow its scope.", code="export_preview_too_large"
            )
        compressed = zlib.compress(snapshot_bytes)
        token = (
            _encode(compressed)
            + "."
            + _encode(hmac.digest(_key(settings.active_jwt_signing_key), compressed, "sha256"))
        )
    return AccountingPreview(
        snapshot_id=snapshot_id,
        contract=CONTRACT,
        scope=scope,
        entity_name=entity.name or entity.legal_name or "Legal entity",
        included=len(rows),
        excluded_unapproved=await _count(
            db, [*base, Invoice.workflow_state.not_in(APPROVED), not_selected]
        ),
        excluded_filters=await _count(db, [*approved, not_selected]),
        excluded_other_currencies=await _count(
            db, [*entity_filters, Invoice.currency != scope.currency, not_selected]
        ),
        unassigned_in_period=await _count(
            db, [*period, Invoice.legal_entity_id.is_(None), not_selected]
        ),
        missing_fx=missing_fx,
        net=str(net),
        tax=str(tax),
        gross=str(gross),
        gross_eur=gross_eur,
        sha256=digest,
        expires_at=expires,
        token=token,
        rows=[
            AccountingPreviewRow(
                invoice_id=inv.id,
                number=inv.invoice_number,
                supplier=row.supplier,
                currency=row.currency,
                net=str(row.subtotal),
                tax=str(row.tax),
                gross=str(row.total),
            )
            for inv, row in zip(invoices, rows, strict=True)
        ],
    )


def download(token: str, org_id: str, user_id: str) -> tuple[str, bytes]:
    try:
        payload, signature = token.split(".")
        compressed, signature_bytes = _decode(payload), _decode(signature)
        keys = (settings.active_jwt_signing_key, *settings.jwt_signing_key_fallbacks)
        if not any(
            hmac.compare_digest(hmac.digest(_key(key), compressed, "sha256"), signature_bytes)
            for key in keys
        ):
            raise ValueError("invalid signature")
        decoder = zlib.decompressobj()
        snapshot_bytes = decoder.decompress(compressed, MAX_BYTES + 1)
        if len(snapshot_bytes) > MAX_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError("oversized or incomplete snapshot")
        data = json.loads(snapshot_bytes)
        if data["contract"] != CONTRACT:
            raise ValueError("unknown contract")
        if data["org_id"] != org_id or data["user_id"] != user_id:
            raise NotFoundError("Export snapshot not found.")
        if data["expires_at"] <= time.time():
            raise ConflictError(
                "This preview expired. Preview the scope again before downloading.",
                code="export_preview_expired",
            )
        content = data["csv"].encode()
        if hashlib.sha256(content).hexdigest() != data["sha256"]:
            raise ValueError("snapshot integrity error")
        return data["filename"], content
    except (ValueError, KeyError, TypeError, zlib.error) as exc:
        raise ValidationError(
            "The export preview is invalid. Create a new preview.", code="export_preview_invalid"
        ) from exc
