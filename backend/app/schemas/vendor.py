from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tax_id: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    category: str | None = Field(default=None, max_length=80)
    iban: str | None = Field(default=None, max_length=42)  # allows spaced input
    bic: str | None = Field(default=None, max_length=11)


class VendorUpdate(BaseModel):
    tax_id: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    category: str | None = Field(default=None, max_length=80)
    iban: str | None = Field(default=None, max_length=42)
    bic: str | None = Field(default=None, max_length=11)
    # Optimistic concurrency (mirrors the invoice-review pattern): the client
    # sends the `version` it read; a mismatch is 409 `stale_version`. `None`
    # opts out (server-internal / legacy callers) — same contract as
    # `invoice_workflow.assert_version`.
    version: int | None = None
    # The vaulted document the new bank/tax value was read from — recorded on
    # the change request so the approver can verify against the source.
    source_document_id: str | None = None


class VendorChangeRequestOut(BaseModel):
    """A protected-field change request. Carries the FULL old/new values —
    the approver must verify the new account against the source document; the
    endpoint is authenticated and the AUDIT trail (not this response) is where
    the full IBAN must never appear. The SPA masks IBANs for display."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    vendor_id: str
    field: str
    old_value: str | None
    new_value: str | None
    status: str
    requested_by: str
    requested_by_email: str | None
    requested_at: datetime
    decided_by: str | None
    decided_by_email: str | None
    decided_at: datetime | None
    decision_note: str | None
    source_document_id: str | None
    # Denormalized for the approval screen (set by the route, not the ORM row).
    vendor_name: str | None = None


class VendorOut(BaseModel):
    """Backward-compatible vendor shape + WO-2 additions (`status`, `version`,
    `pending_changes`). RESPONSE-SHAPE DECISION (WO-2 step 4): a PATCH that
    touches a protected field returns **200 with the UNCHANGED vendor plus the
    `pending_changes` block** — not 202 — because one PATCH may mix protected
    and non-protected fields; the non-protected ones apply immediately and the
    caller needs the resulting row. The pending value is NEVER echoed in the
    vendor fields themselves — only inside `pending_changes`, explicitly marked
    `status="pending"`."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    tax_id: str | None
    country: str | None
    category: str | None
    iban: str | None = None
    bic: str | None = None
    status: str = "active"
    version: int = 1
    pending_changes: list[VendorChangeRequestOut] = []


class ChangeDecisionIn(BaseModel):
    """Approve/reject body. The note is required on reject (the audit trail
    records WHY a captured value was refused); optional on approve."""

    note: str | None = Field(default=None, max_length=2000)
