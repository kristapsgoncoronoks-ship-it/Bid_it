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


class VendorRefOut(BaseModel):
    """Another vendor of the same workspace, named — DB-014's collision
    surface points at it."""

    id: str
    name: str


class VendorChangeRequestOut(BaseModel):
    """A protected-field change request. Carries the FULL old/new values —
    the approver must verify the new account against the source document; the
    endpoint is authenticated and the AUDIT trail (not this response) is where
    the full IBAN must never appear. The SPA masks IBANs for display.

    `shared_with` (DB-014): for a request on the `iban` field, the OTHER vendors
    of this workspace whose STORED account is the one this request names — the
    approver sees the collision before applying it, and a decided request still
    says what it pointed at. Empty for other fields. Computed at read time, so
    it is current, and bounded (`services.vendors.HOLDER_LIMIT`)."""

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
    shared_with: list[VendorRefOut] = []


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
    # DB-014: the other vendors of this workspace whose stored IBAN is this
    # vendor's — shown, never refused (DECISIONS §26).
    iban_shared_with: list[VendorRefOut] = []


class ChangeDecisionIn(BaseModel):
    """Approve/reject body. The note is required on reject (the audit trail
    records WHY a captured value was refused); optional on approve."""

    note: str | None = Field(default=None, max_length=2000)


class VendorCandidateOut(BaseModel):
    """A supplier that ALMOST matches the captured name, with the reason it is
    close. A reason, not a similarity score: "87% similar" tells the operator
    nothing they can check, whereas "the same name apart from the company-form
    suffix" is something they can agree or disagree with."""

    vendor_id: str
    name: str
    near_kind: str
    reason: str


class VendorResolutionOut(BaseModel):
    """H-3 — how a captured supplier name resolves, and why.

    `needs_decision` means the machine ABSTAINED: nothing matched exactly, but
    something nearly did, so nothing has been chosen. `outcome` states plainly
    what confirming unchanged will do — including "a NEW supplier will be
    created", which is the consequence an operator most needs to see BEFORE it
    happens rather than after."""

    captured_name: str
    basis: str  # exact_name | none
    reason: str
    vendor_id: str | None = None
    vendor_name: str | None = None
    needs_decision: bool = False
    candidates: list[VendorCandidateOut] = Field(default_factory=list)
    outcome: str = ""
