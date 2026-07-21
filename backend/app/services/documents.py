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


async def store(prefix: str, org_id: str, data: bytes, content_type: str | None = None) -> tuple[str, int]:
    """Persist bytes; return (sha256, size). Idempotent — same bytes ⇒ same key."""
    sha = storage.sha256_hex(data)
    key = storage.content_key(prefix, org_id, sha)
    await run_in_threadpool(storage.get_storage().put, key, data, content_type)
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
