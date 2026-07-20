"""Tenant-aware helpers for storing/loading document bytes via object storage.

The one place the app writes/reads binary originals. Handlers call these instead
of touching `core.storage` directly, so:
  • storage I/O runs off the event loop (threadpool),
  • keys are content-addressed + tenant-prefixed consistently,
  • reads fall back to the LEGACY in-DB blob during the migration window (dual
    read), so pre-existing rows keep working until a later contract migration
    drops the `*_data` columns.

`prefix` names the document class (`receipts`, `logos`, `email-attachments`).
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


async def load(prefix: str, org_id: str, sha256: str | None, *, legacy: bytes | None = None) -> bytes | None:
    """Load bytes for a stored object. Prefers object storage (when `sha256` is
    set); otherwise returns the legacy in-DB blob (`legacy`) if present."""
    if sha256:
        key = storage.content_key(prefix, org_id, sha256)
        try:
            return await run_in_threadpool(storage.get_storage().get, key)
        except storage.StorageError:
            # Fall back to the legacy blob if the object is somehow missing.
            if legacy is not None:
                return legacy
            raise
    return legacy


async def delete(prefix: str, org_id: str, sha256: str | None) -> None:
    if sha256:
        key = storage.content_key(prefix, org_id, sha256)
        await run_in_threadpool(storage.get_storage().delete, key)


# Canonical document-class prefixes.
RECEIPTS = "receipts"
LOGOS = "logos"
EMAIL_ATTACHMENTS = "email-attachments"
