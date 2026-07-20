"""Email invoice intake — inbound address custody + attachment processing.

Each org gets a unique inbound address `<token>@<inbound-domain>`. An email
provider's inbound-parse webhook posts the message (with attachments) to the app;
every attachment is run through the SAME extraction engine (`parser.parse_invoice_file`)
as a manual upload and lands in a per-org review inbox as an `InboundInvoice`. A
user reviews the parsed draft and confirms it into a real `Invoice` — nothing is
persisted as an invoice without that human confirm (deterministic-first, review-gated).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.email_intake import EmailIntake, InboundInvoice
from app.services import filesec
from app.services.parser import parse_invoice_file


def _new_token() -> str:
    # URL/address-safe, lowercase hex — unambiguous as an email local-part.
    return secrets.token_hex(8)


def address_for(token: str) -> str:
    return f"{token}@{settings.inbound_email_domain}"


def token_from_address(addr: str | None) -> str | None:
    """Extract the token (local-part) from an inbound `to` address. Tolerates a
    `Name <token@domain>` form and a bare token."""
    if not addr:
        return None
    value = addr.strip()
    if "<" in value and ">" in value:
        value = value[value.index("<") + 1 : value.index(">")]
    value = value.strip()
    local = value.split("@", 1)[0].strip().lower()
    return local or None


async def get_or_create(db: AsyncSession, org_id: str) -> EmailIntake:
    row = await db.scalar(select(EmailIntake).where(EmailIntake.org_id == org_id))
    if row is not None:
        return row
    row = EmailIntake(org_id=org_id, token=_new_token())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def rotate(db: AsyncSession, org_id: str) -> EmailIntake:
    row = await get_or_create(db, org_id)
    row.token = _new_token()
    await db.commit()
    await db.refresh(row)
    return row


async def resolve_org(db: AsyncSession, token: str | None) -> str | None:
    """Map an inbound token to its org id. Runs unscoped (the webhook is
    unauthenticated), so it can find any tenant's intake config."""
    if not token:
        return None
    return await db.scalar(select(EmailIntake.org_id).where(EmailIntake.token == token.lower()))


def _method_for(filename: str) -> str:
    lower = filename.lower()
    for ext in ("pdf", "xml", "csv", "json"):
        if lower.endswith("." + ext):
            return ext
    return "unknown"


async def process_attachment(
    db: AsyncSession,
    org_id: str,
    *,
    from_addr: str | None,
    subject: str | None,
    filename: str,
    content_type: str | None,
    content: bytes,
) -> InboundInvoice:
    """Parse one email attachment into a review-inbox row (best-effort). A parse
    failure is recorded as a `failed` row (with the reason), never raised — one
    bad attachment must not drop the rest of the message.

    Security gate FIRST: a malicious attachment (malware, executable, script
    disguised as an invoice, wrong type) is marked `rejected` with the reason and
    its bytes are NOT stored — never parsed, never OCR'd, never vaulted."""
    sha = hashlib.sha256(content).hexdigest()
    row = InboundInvoice(
        org_id=org_id,
        from_addr=(from_addr or None) and from_addr[:320],
        subject=(subject or None) and subject[:500],
        received_at=datetime.now(timezone.utc),
        filename=(filename or "attachment")[:300],
        content_type=(content_type or None) and content_type[:120],
        size=len(content),
        sha256=sha,
        method=_method_for(filename or ""),
        file_data=None,
    )
    try:
        filesec.check(filename or "attachment", content, allowed=filesec.INVOICE_KINDS)
    except filesec.FileRejected as exc:
        # Quarantine: keep the metadata for the audit trail, drop the bytes.
        row.status = "rejected"
        row.error = str(exc)
        db.add(row)
        return row

    # Passed the security gate → safe to retain the original and parse it.
    row.file_data = content
    try:
        # CPU-bound OCR/parse off the event loop (the webhook loops attachments).
        draft = await run_in_threadpool(parse_invoice_file, filename or "attachment", content)
        row.draft_json = draft.model_dump_json()
        # Record HOW it was read (e-invoice-xml | text-layer | ocr | csv | json)
        # so the review inbox shows the extraction method, not just the file type.
        row.method = draft.method if draft.method and draft.method != "unknown" else row.method
        row.status = "pending"
    except ValueError as exc:
        row.status = "failed"
        row.error = str(exc)
    db.add(row)
    return row
