from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.api.routes.invoices import _detail, persist_invoice
from app.core.config import settings
from app.core.tenant import reset_current_org, set_current_org
from app.models.email_intake import InboundInvoice
from app.models.organization import Organization
from app.models.user import UserRole
from app.schemas.email_intake import (
    EmailSettingsOut,
    InboundConfirm,
    InboundEmailIn,
    InboundInvoiceDetail,
    InboundInvoiceOut,
    InboundListOut,
    InboundResult,
)
from app.schemas.invoice import InvoiceDetailOut, ParsedInvoiceDraft
from app.services import email_intake, modules

router = APIRouter(prefix="/email", tags=["email intake"])


async def _guard(db: DbSession, org_id: str):
    if not await modules.is_enabled(db, org_id, "email_intake"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The email invoice intake module is not activated.")


# --------------------------------------------------------------------------- #
# Inbound webhook (public — called by the email provider's parse hook)
# --------------------------------------------------------------------------- #
@router.post("/inbound", response_model=InboundResult)
async def inbound(
    body: InboundEmailIn,
    db: DbSession,
    x_inbound_secret: str | None = Header(default=None, alias="X-Inbound-Secret"),
):
    """Receive a parsed inbound email and drop each attachment into the org's
    review inbox. Unauthenticated: the tenant is resolved from the inbound
    address token, and (optionally) a shared secret authenticates the provider."""
    expected = settings.inbound_email_secret
    if expected:
        presented = x_inbound_secret or body.secret
        if presented != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid inbound secret")

    token = body.token or email_intake.token_from_address(body.to)
    org_id = await email_intake.resolve_org(db, token)
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown inbound address")

    # Module gate for the resolved tenant.
    if not await modules.is_enabled(db, org_id, "email_intake"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Email invoice intake is not activated for this workspace")

    pending = failed = 0
    scope = set_current_org(org_id)
    try:
        for att in body.attachments:
            try:
                content = base64.b64decode(att.content_base64, validate=True)
            except (binascii.Error, ValueError):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Attachment {att.filename} is not valid base64")
            row = await email_intake.process_attachment(
                db, org_id,
                from_addr=body.from_addr, subject=body.subject,
                filename=att.filename, content_type=att.content_type, content=content,
            )
            if row.status == "failed":
                failed += 1
            else:
                pending += 1
        await db.commit()
    finally:
        reset_current_org(scope)

    return InboundResult(received=len(body.attachments), pending=pending, failed=failed)


# --------------------------------------------------------------------------- #
# Settings (the inbound address)
# --------------------------------------------------------------------------- #
@router.get("/settings", response_model=EmailSettingsOut)
async def get_settings(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    intake = await email_intake.get_or_create(db, current.org_id)
    total = await db.scalar(select(func.count(InboundInvoice.id)).where(InboundInvoice.org_id == current.org_id)) or 0
    pending = await db.scalar(
        select(func.count(InboundInvoice.id)).where(
            InboundInvoice.org_id == current.org_id, InboundInvoice.status == "pending"
        )
    ) or 0
    return EmailSettingsOut(
        address=email_intake.address_for(intake.token),
        domain=settings.inbound_email_domain,
        pending=pending, total=total,
    )


@router.post("/settings/rotate", response_model=EmailSettingsOut)
async def rotate_address(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    if current.role != UserRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the workspace owner can rotate the inbound address")
    intake = await email_intake.rotate(db, current.org_id)
    return EmailSettingsOut(address=email_intake.address_for(intake.token), domain=settings.inbound_email_domain)


# --------------------------------------------------------------------------- #
# Review inbox
# --------------------------------------------------------------------------- #
async def _load(db: DbSession, org_id: str, inbound_id: str) -> InboundInvoice:
    row = await db.scalar(
        select(InboundInvoice).where(InboundInvoice.id == inbound_id, InboundInvoice.org_id == org_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound invoice not found")
    return row


@router.get("/inbox", response_model=InboundListOut)
async def list_inbox(
    current: CurrentUser,
    db: DbSession,
    status_: str | None = Query(default=None, alias="status"),
):
    await _guard(db, current.org_id)
    filters = [InboundInvoice.org_id == current.org_id]
    if status_:
        filters.append(InboundInvoice.status == status_)
    total = await db.scalar(select(func.count(InboundInvoice.id)).where(*filters)) or 0
    rows = await db.scalars(
        select(InboundInvoice).where(*filters).order_by(InboundInvoice.received_at.desc())
    )
    return InboundListOut(items=[InboundInvoiceOut.model_validate(r) for r in rows], total=total)


@router.get("/inbox/{inbound_id}", response_model=InboundInvoiceDetail)
async def get_inbound(inbound_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    row = await _load(db, current.org_id, inbound_id)
    draft = None
    if row.draft_json:
        try:
            draft = ParsedInvoiceDraft.model_validate_json(row.draft_json)
        except ValueError:
            draft = None
    detail = InboundInvoiceDetail.model_validate(row)
    detail.draft = draft
    detail.has_file = row.file_data is not None
    return detail


@router.post("/inbox/{inbound_id}/confirm", response_model=InvoiceDetailOut, status_code=status.HTTP_201_CREATED)
async def confirm_inbound(inbound_id: str, body: InboundConfirm, current: CurrentUser, db: DbSession):
    """Confirm a parsed inbound invoice into a real Invoice — using the same
    persistence path as a manual upload. Accepts an edited draft override."""
    await _guard(db, current.org_id)
    row = await _load(db, current.org_id, inbound_id)
    if row.status == "confirmed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This invoice was already confirmed")

    draft = body.draft
    if draft is None:
        if not row.draft_json:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This attachment has no parsed draft to confirm")
        draft = ParsedInvoiceDraft.model_validate_json(row.draft_json).draft

    invoice, vendor_name = await persist_invoice(db, current.org_id, draft)
    row.invoice_id = invoice.id
    row.status = "confirmed"
    await db.commit()
    return _detail(invoice, vendor_name)


@router.post("/inbox/{inbound_id}/discard", response_model=InboundInvoiceOut)
async def discard_inbound(inbound_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    row = await _load(db, current.org_id, inbound_id)
    if row.status == "confirmed":
        raise HTTPException(status.HTTP_409_CONFLICT, "A confirmed invoice cannot be discarded")
    row.status = "discarded"
    await db.commit()
    await db.refresh(row)
    return InboundInvoiceOut.model_validate(row)


@router.delete("/inbox/{inbound_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbound(inbound_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    row = await _load(db, current.org_id, inbound_id)
    await db.delete(row)
    await db.commit()


@router.get("/inbox/{inbound_id}/file")
async def download_file(inbound_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    row = await _load(db, current.org_id, inbound_id)
    if not row.file_data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No stored file for this attachment")
    fname = row.filename or "attachment"
    return Response(
        content=row.file_data,
        media_type=row.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
