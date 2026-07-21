"""Tenant-aware helpers for storing/loading document bytes via object storage.

The one place the app writes/reads binary originals. Handlers call these instead
of touching `core.storage` directly, so:
  • storage I/O runs off the event loop (threadpool),
  • keys are content-addressed + tenant-prefixed consistently.

Object storage is the sole home for document bytes: the legacy in-DB `*_data`
blob columns and their dual-read fallback were dropped once the migration window
closed (ADR-0008). `prefix` names the document class (`receipts`, `logos`,
`email-attachments`).
"""

from __future__ import annotations

from fastapi.concurrency import run_in_threadpool

from app.core import storage


async def store(
    prefix: str,
    org_id: str,
    data: bytes,
    content_type: str | None = None,
    *,
    db=None,
    filename: str | None = None,
    uploaded_by: str | None = None,
) -> tuple[str, int]:
    """Persist bytes; return (sha256, size). Idempotent — same bytes ⇒ same key.

    When a `db` session is passed, the object is also recorded in the document
    registry (Slice 5d) in that session — the caller's own commit persists it. This
    is THE choke point for binary writes, so every upload path registers here."""
    sha = storage.sha256_hex(data)
    key = storage.content_key(prefix, org_id, sha)
    await run_in_threadpool(storage.get_storage().put, key, data, content_type)
    if db is not None:
        from app.services import document_registry

        await document_registry.register(
            db,
            org_id,
            sha256=sha,
            size=len(data),
            kind=prefix,
            mime=content_type,
            filename=filename,
            uploaded_by=uploaded_by,
        )
    return sha, len(data)


async def load(prefix: str, org_id: str, sha256: str | None) -> bytes | None:
    """Load bytes for a stored object from object storage. Returns None when
    there is no reference (`sha256` is None); raises `StorageError` when a
    referenced object is missing (an integrity fault, surfaced by `verify`)."""
    if not sha256:
        return None
    key = storage.content_key(prefix, org_id, sha256)
    return await run_in_threadpool(storage.get_storage().get, key)


async def delete(prefix: str, org_id: str, sha256: str | None) -> None:
    if sha256:
        key = storage.content_key(prefix, org_id, sha256)
        await run_in_threadpool(storage.get_storage().delete, key)


# Canonical document-class prefixes.
RECEIPTS = "receipts"
LOGOS = "logos"
EMAIL_ATTACHMENTS = "email-attachments"
UPLOADS = "uploads"  # UI direct uploads, persisted so the worker can parse off-tier
