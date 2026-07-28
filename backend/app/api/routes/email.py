from __future__ import annotations

import base64
import binascii
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import CurrentOrg, CurrentUser, DbSession, require_perm
from app.api.routes.invoices import _detail, persist_invoice
from app.core import authz
from app.core.config import settings
from app.core.errors import AppError
from app.core.tenant import reset_current_org, set_current_org
from app.models.email_intake import InboundInvoice
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
from app.services import access, audit, documents, email_intake, modules
from app.services.email_providers import mailgun

# Structural authorization (ADR-0024): declared PER-ROUTE because POST /inbound
# is a provider webhook (its own authentication; see PUBLIC_ROUTES). The review
# inbox is the metered capture surface — INVOICE_READ (held by EVERY business
# role) preserves the documented open-to-every-tier capture decision (see
# `confirm_inbound`); rotating the inbound address is org configuration.
router = APIRouter(prefix="/email", tags=["email intake"])
_CAPTURE = [Depends(require_perm(authz.Permission.INVOICE_READ))]
_ADMIN = [Depends(require_perm(authz.Permission.SETTINGS_MANAGE))]


async def _guard(db: DbSession, org_id: str):
    await modules.require_enabled(db, org_id, "email_intake")


# --------------------------------------------------------------------------- #
# Inbound webhook (public — called by the email provider's parse hook)
# --------------------------------------------------------------------------- #
def _inbound_auth_failed() -> AppError:
    """The ONE rejection for every inbound-auth failure mode (no secret
    configured, secret absent, secret wrong, unknown recipient token). A single
    construction site guarantees the responses are byte-identical, so a caller
    can never distinguish "bad secret" from "real tenant, wrong secret" from
    "no such tenant" — enumeration safety over the 64-bit address tokens."""
    return AppError(
        "Inbound authentication failed",
        code="inbound_auth_failed",
        status=status.HTTP_401_UNAUTHORIZED,
    )


@router.post("/inbound", response_model=InboundResult)
async def inbound(
    body: InboundEmailIn,
    db: DbSession,
    x_inbound_secret: str | None = Header(default=None, alias="X-Inbound-Secret"),
):
    """Receive a parsed inbound email and drop each attachment into the org's
    review inbox.

    Authentication is a MANDATORY shared secret (header `X-Inbound-Secret` or a
    `secret` body field) presented by the email provider's webhook. FAIL CLOSED:
    an unconfigured secret rejects everything — an unset env var must never
    silently open a document-injection door into a tenant's AP review flow. The
    comparison is constant-time (`hmac.compare_digest`) so it leaks neither
    length nor prefix.

    The tenant is resolved from the RECIPIENT address token, never the sender:
    `From` is trivially forgeable and forwarding breaks SPF/DKIM alignment, so
    the sender can never be an identity. An unknown recipient token returns the
    SAME 401 as a bad secret (never a distinguishable 404) so the endpoint
    cannot be used to enumerate live inbound addresses."""
    expected = settings.inbound_email_secret
    presented = x_inbound_secret or body.secret
    if not expected or presented is None:
        raise _inbound_auth_failed()
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise _inbound_auth_failed()

    token = body.token or email_intake.token_from_address(body.to)
    org_id = await email_intake.resolve_org(db, token)
    if org_id is None:
        raise _inbound_auth_failed()

    # Module gate for the resolved tenant.
    if not await modules.is_enabled(db, org_id, "email_intake"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Email invoice intake is not activated for this workspace"
        )

    queued = rejected = 0
    scope = set_current_org(org_id)
    try:
        for att in body.attachments:
            try:
                content = base64.b64decode(att.content_base64, validate=True)
            except (binascii.Error, ValueError):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Attachment {att.filename} is not valid base64",
                )
            row = await email_intake.process_attachment(
                db,
                org_id,
                from_addr=body.from_addr,
                subject=body.subject,
                filename=att.filename,
                content_type=att.content_type,
                content=content,
            )
            if row.status == "rejected":
                rejected += 1
            else:
                queued += 1
        # Rows + their extract jobs commit together; the worker parses out-of-band.
        await db.commit()
    finally:
        reset_current_org(scope)

    return InboundResult(received=len(body.attachments), queued=queued, rejected=rejected)


@router.post("/inbound/mailgun", response_model=InboundResult)
async def inbound_mailgun(request: Request, db: DbSession):
    """Mailgun-native counterpart to `POST /inbound` (E1.6 — the email-intake
    provider adapter). Reads Mailgun's own inbound-route `multipart/form-data`
    payload directly (`recipient`, `sender`, `subject`, `timestamp`, `token`,
    `signature`, `attachment-count` + `attachment-1..N` file parts) instead of
    requiring the caller to have already normalised it into `InboundEmailIn`,
    and feeds each attachment through the SAME `email_intake.process_attachment`
    pipeline the generic endpoint above uses — no logic is duplicated.

    Authentication is a SECOND, provider-native layer on top of the shared
    recipient-token tenant resolution both endpoints use: Mailgun's own
    HMAC-SHA256(signing_key, timestamp+token) signature
    (`app.services.email_providers.mailgun.verify_signature`), plus a freshness
    window (`mailgun.is_fresh`) so a captured valid signature cannot be replayed
    indefinitely. FAILS CLOSED exactly like `/inbound`: an unconfigured signing
    key, a missing/bad/stale signature, or an unresolvable recipient token all
    return the SAME `_inbound_auth_failed()` response — a prober can never
    distinguish "wrong signature" from "no such tenant" from "Mailgun not
    configured"."""
    form = await request.form()

    def _s(key: str) -> str | None:
        value = form.get(key)
        return value if isinstance(value, str) else None

    signing_key = settings.mailgun_signing_key
    token_field = _s("token") or ""
    timestamp = _s("timestamp") or ""
    signature = _s("signature") or ""
    if (
        not signing_key
        or not mailgun.verify_signature(
            token=token_field, timestamp=timestamp, signature=signature, signing_key=signing_key
        )
        or not mailgun.is_fresh(timestamp)
    ):
        raise _inbound_auth_failed()

    recipient = _s("recipient") or _s("to")
    org_id = await email_intake.resolve_org(db, email_intake.token_from_address(recipient))
    if org_id is None:
        raise _inbound_auth_failed()

    if not await modules.is_enabled(db, org_id, "email_intake"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Email invoice intake is not activated for this workspace"
        )

    sender = _s("sender") or _s("from")
    subject = _s("subject")
    try:
        count = int(_s("attachment-count") or "0")
    except ValueError:
        count = 0

    queued = rejected = 0
    scope = set_current_org(org_id)
    try:
        for i in range(1, count + 1):
            upload = form.get(f"attachment-{i}")
            if upload is None or isinstance(upload, str):
                continue  # malformed part — never a reason to fail the whole message
            content = await upload.read()
            row = await email_intake.process_attachment(
                db,
                org_id,
                from_addr=sender,
                subject=subject,
                filename=upload.filename or f"attachment-{i}",
                content_type=upload.content_type,
                content=content,
            )
            if row.status == "rejected":
                rejected += 1
            else:
                queued += 1
        await db.commit()
    finally:
        reset_current_org(scope)

    return InboundResult(received=count, queued=queued, rejected=rejected)


# --------------------------------------------------------------------------- #
# Settings (the inbound address)
# --------------------------------------------------------------------------- #
@router.get("/settings", response_model=EmailSettingsOut, dependencies=_CAPTURE)
async def get_settings(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    intake = await email_intake.get_or_create(db, current.org_id)
    total = (
        await db.scalar(
            select(func.count(InboundInvoice.id)).where(InboundInvoice.org_id == current.org_id)
        )
        or 0
    )
    pending = (
        await db.scalar(
            select(func.count(InboundInvoice.id)).where(
                InboundInvoice.org_id == current.org_id, InboundInvoice.status == "pending"
            )
        )
        or 0
    )
    return EmailSettingsOut(
        address=email_intake.address_for(intake.token),
        domain=settings.inbound_email_domain,
        pending=pending,
        total=total,
    )


@router.post("/settings/rotate", response_model=EmailSettingsOut, dependencies=_ADMIN)
async def rotate_address(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    intake = await email_intake.rotate(db, current.org_id)
    return EmailSettingsOut(
        address=email_intake.address_for(intake.token), domain=settings.inbound_email_domain
    )


# --------------------------------------------------------------------------- #
# Review inbox
# --------------------------------------------------------------------------- #
async def _load(db: DbSession, org_id: str, inbound_id: str) -> InboundInvoice:
    row = await db.scalar(
        select(InboundInvoice).where(
            InboundInvoice.id == inbound_id, InboundInvoice.org_id == org_id
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound invoice not found")
    return row


@router.get("/inbox", response_model=InboundListOut, dependencies=_CAPTURE)
async def list_inbox(
    current: CurrentUser,
    db: DbSession,
    status_: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
):
    await _guard(db, current.org_id)
    filters = [InboundInvoice.org_id == current.org_id]
    if status_:
        filters.append(InboundInvoice.status == status_)
    total = await db.scalar(select(func.count(InboundInvoice.id)).where(*filters)) or 0
    rows = await db.scalars(
        select(InboundInvoice)
        .where(*filters)
        .order_by(InboundInvoice.received_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return InboundListOut(items=[InboundInvoiceOut.model_validate(r) for r in rows], total=total)


@router.get("/inbox/{inbound_id}", response_model=InboundInvoiceDetail, dependencies=_CAPTURE)
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
    # A rejected (quarantined) attachment carries a sha256 for the audit trail but
    # its bytes were never retained — so it has no downloadable file.
    detail.has_file = row.status != "rejected" and row.sha256 is not None
    return detail


@router.post(
    "/inbox/{inbound_id}/confirm",
    response_model=InvoiceDetailOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CAPTURE,
)
async def confirm_inbound(
    inbound_id: str,
    body: InboundConfirm,
    current: CurrentUser,
    current_org: CurrentOrg,
    db: DbSession,
):
    """Confirm a parsed inbound invoice into a real Invoice — using the same
    persistence path as a manual upload. Accepts an edited draft override."""
    # Same metered capture flow as /invoices and /invoices/upload — quota-governed,
    # open to every tier; not INVOICE_WRITE-gated. WO-47: keyed by the ORG's
    # plan, not the caller's role.
    await _guard(db, current.org_id)
    await access.enforce_invoice_quota(db, current.org_id, current_org.plan)
    row = await _load(db, current.org_id, inbound_id)
    if row.status == "confirmed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This invoice was already confirmed")

    draft = body.draft
    if draft is None:
        if not row.draft_json:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "This attachment has no parsed draft to confirm",
            )
        draft = ParsedInvoiceDraft.model_validate_json(row.draft_json).draft

    invoice, vendor_name = await persist_invoice(db, current.org_id, draft)
    row.invoice_id = invoice.id
    row.status = "confirmed"
    await audit.record(
        db,
        audit.A.INBOUND_CONFIRM,
        target_type="invoice",
        target_id=invoice.id,
        meta={"inbound_id": inbound_id, "number": invoice.invoice_number},
    )
    await db.commit()
    return _detail(invoice, vendor_name)


@router.post("/inbox/{inbound_id}/discard", response_model=InboundInvoiceOut, dependencies=_CAPTURE)
async def discard_inbound(inbound_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    row = await _load(db, current.org_id, inbound_id)
    if row.status == "confirmed":
        raise HTTPException(status.HTTP_409_CONFLICT, "A confirmed invoice cannot be discarded")
    row.status = "discarded"
    await db.commit()
    await db.refresh(row)
    return InboundInvoiceOut.model_validate(row)


@router.delete("/inbox/{inbound_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_CAPTURE)
async def delete_inbound(inbound_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    row = await _load(db, current.org_id, inbound_id)
    await db.delete(row)
    await db.commit()


@router.get("/inbox/{inbound_id}/file", dependencies=_CAPTURE)
async def download_file(inbound_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    row = await _load(db, current.org_id, inbound_id)
    if row.status == "rejected" or not row.sha256:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No stored file for this attachment")
    content = await documents.load(documents.EMAIL_ATTACHMENTS, current.org_id, row.sha256)
    await audit.record(
        db,
        audit.A.DOC_DOWNLOAD,
        target_type="inbound_invoice",
        target_id=inbound_id,
        meta={"filename": row.filename},
    )
    await db.commit()
    fname = (row.filename or "attachment").replace('"', "")
    # Serve inert: force download, never inline, and stop MIME sniffing.
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
