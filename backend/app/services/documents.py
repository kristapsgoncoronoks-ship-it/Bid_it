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


async def load(
    prefix: str, org_id: str, sha256: str | None, *, max_bytes: int | None = None
) -> bytes | None:
    """Load bytes for a stored object from object storage. Returns None when
    there is no reference (`sha256` is None); raises `StorageError` when a
    referenced object is missing (an integrity fault, surfaced by `verify`)."""
    if not sha256:
        return None
    key = storage.content_key(prefix, org_id, sha256)
    if max_bytes is None:
        return await run_in_threadpool(storage.get_storage().get, key)
    return await run_in_threadpool(storage.get_storage().get, key, max_bytes=max_bytes)


async def load_capture(prefix: str, org_id: str, sha256: str | None) -> bytes | None:
    """Bound parser input, then verify the original identity before parsing."""
    from app.services import capture_failures, filesec

    try:
        content = await load(prefix, org_id, sha256, max_bytes=filesec.max_bytes())
    except storage.StorageReadTooLarge as exc:
        raise capture_failures.CaptureError(
            capture_failures.STORED_FILE_TOO_LARGE,
            "Stored document exceeds the processing size limit.",
        ) from exc
    if content is not None and await run_in_threadpool(storage.sha256_hex, content) != sha256:
        raise capture_failures.CaptureError(
            capture_failures.STORED_FILE_CHANGED,
            "Stored document does not match the original. Restore the original before retrying.",
        )
    return content


async def digest(prefix: str, org_id: str, sha256: str | None) -> str | None:
    """Re-hash a historical original without materializing its bytes in memory.

    The storage backend opens, reads and closes its own source in the worker
    thread. This bounds hashing memory, not total I/O duration or source size.
    """
    if not sha256:
        return None
    key = storage.content_key(prefix, org_id, sha256)
    return await run_in_threadpool(storage.get_storage().digest, key)


async def delete(prefix: str, org_id: str, sha256: str | None) -> None:
    if sha256:
        key = storage.content_key(prefix, org_id, sha256)
        await run_in_threadpool(storage.get_storage().delete, key)


# Canonical document-class prefixes.
RECEIPTS = "receipts"
LOGOS = "logos"
EMAIL_ATTACHMENTS = "email-attachments"
UPLOADS = "uploads"  # UI direct uploads, persisted so the worker can parse off-tier
INVOICE_ATTACHMENTS = "invoice-attachments"  # internal working docs on an invoice (Phase 08)
ISSUED_ATTACHMENTS = "issued-attachments"  # supporting docs on an issued invoice (INV-4)
# WO-AF: the fuel-card statement bytes a review finding points at. Until this
# prefix existed the statement was digested, keyed on everywhere (findings,
# baselines, audit) and then discarded — a finding named a file nobody could
# open. Vaulted through THIS choke point like every other original.
STATEMENTS = "statements"
EXPORTS = "exports"  # WO-AI: a whole-archive zip produced for its owner, one-time link
