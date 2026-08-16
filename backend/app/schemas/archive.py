"""Wire shapes for the client-facing platform archive."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArchivedInvoiceOut(BaseModel):
    """One archived invoice, as the client's owner sees it.

    Deliberately NOT the live `InvoiceOut` shape. An archived record is a
    historical fact, not a workflow object: there is no status to change, no
    approval to give and nothing to pay, so exposing those fields would invite a
    UI that offers actions the archive cannot perform.
    """

    id: str
    # The id it had while live — ties this row to the audit event that recorded
    # its deletion, and to any support ticket quoting it.
    original_invoice_id: str
    invoice_number: str | None = None
    vendor_name: str | None = None
    issue_date: str | None = None
    currency: str | None = None
    total: str | None = None
    line_items: list[dict] = Field(default_factory=list)
    # Whether the source document survived with the record. False for an invoice
    # that was keyed in by hand and never had one.
    has_document: bool = False
    source_filename: str | None = None
    # When the CLIENT deleted it, carried over from the bin. Named
    # `original_*` because on this table `deleted_at` would read as "this archive
    # row is deleted", which is the opposite of what it means.
    original_deleted_at: str | None = None
    original_deleted_by: str | None = None
    archived_at: str
    expires_at: str


class ArchiveListOut(BaseModel):
    items: list[ArchivedInvoiceOut] = Field(default_factory=list)
    total: int
    # Stated by the server so the promise on screen has exactly one source —
    # the same rule the Trash screen follows.
    retention_years: int
    # How far ahead of expiry a record counts as "leaving soon". Published for
    # the same reason as `retention_years`: the screen highlights rows inside
    # this window, and a client-side constant would keep flagging 60 days the
    # day the archive warns at 90 — the drift the Trash screen was built to
    # avoid, on the figure that decides whether somebody extends in time.
    expiry_notice_days: int
