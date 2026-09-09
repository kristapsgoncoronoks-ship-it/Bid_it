"""PROD-009 — one whole-workspace export request as its owner sees it."""

from __future__ import annotations

from pydantic import BaseModel


class WorkspaceExportOut(BaseModel):
    """No link ever crosses this wire: the one-time link goes to the requesting
    owner's address by email, so reading this list is not a way to obtain one."""

    id: str
    status: str  # queued | ready | failed | downloaded
    requested_email: str
    created_at: str
    ready_at: str | None = None
    link_expires_at: str | None = None
    downloaded_at: str | None = None
    #: Set once the bytes have been destroyed by the daily artefact purge. The
    #: row stays as the record that an export happened.
    purged_at: str | None = None
    rows: int | None = None
    tables: int | None = None
    documents: int | None = None
    missing_documents: int | None = None
    size: int | None = None
    error: str | None = None
