from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.invoice import InvoiceCreate, ParsedInvoiceDraft


class EmailSettingsOut(BaseModel):
    address: str
    domain: str
    # Counts for the inbox badge.
    pending: int = 0
    total: int = 0


class InboundAttachment(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    content_type: str | None = Field(default=None, max_length=120)
    content_base64: str = Field(min_length=1)


class InboundEmailIn(BaseModel):
    """Inbound webhook payload from an email provider's parse hook. `token` may be
    given explicitly or derived from the recipient (`to`) address."""

    to: str | None = None
    token: str | None = None
    from_addr: str | None = Field(default=None, alias="from")
    subject: str | None = None
    secret: str | None = None
    attachments: list[InboundAttachment] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class InboundResult(BaseModel):
    received: int
    # Accepted for extraction (bytes stored, an extract job enqueued). Parsing
    # runs on the worker tier, so the parsed/failed outcome appears in the inbox
    # afterwards, not in this synchronous webhook response.
    queued: int
    rejected: int = 0   # blocked by the security gate (malware / bad type)


class InboundInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    from_addr: str | None
    subject: str | None
    received_at: datetime
    filename: str
    content_type: str | None
    size: int
    status: str
    method: str | None
    error: str | None
    invoice_id: str | None
    created_at: datetime


class InboundInvoiceDetail(InboundInvoiceOut):
    draft: ParsedInvoiceDraft | None = None
    has_file: bool = False


class InboundListOut(BaseModel):
    items: list[InboundInvoiceOut]
    total: int


class InboundConfirm(BaseModel):
    """Optional edited draft to confirm with. When omitted, the stored parsed
    draft is used as-is."""

    draft: InvoiceCreate | None = None
