"""Document-registry service (Slice 5d).

`register` upserts one row per distinct `(org, sha256, kind)` stored object. It is
called from the storage choke point (`documents.store`) and does NOT commit — the
caller's own commit (the receipt save, the logo save, the attachment job)
persists it in the same transaction.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document


async def register(
    db: AsyncSession,
    org_id: str,
    *,
    sha256: str,
    size: int,
    kind: str,
    mime: str | None = None,
    filename: str | None = None,
    uploaded_by: str | None = None,
) -> Document:
    """Record (or touch) the metadata for a stored object. Content-addressed, so a
    re-store of the same bytes under the same kind updates the existing row rather
    than duplicating. Does not commit."""
    existing = await db.scalar(
        select(Document).where(
            Document.org_id == org_id, Document.sha256 == sha256, Document.kind == kind
        )
    )
    if existing is not None:
        if filename:
            existing.filename = filename
        if mime:
            existing.mime = mime
        return existing
    doc = Document(
        org_id=org_id,
        sha256=sha256,
        size=size,
        kind=kind,
        mime=mime,
        filename=filename,
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    # Flush so a second store of the SAME bytes+kind in this transaction sees it
    # (autoflush is off) and touches it rather than inserting a duplicate.
    await db.flush()
    return doc


async def list_for_org(db: AsyncSession, org_id: str, *, kind: str | None = None) -> list[Document]:
    stmt = select(Document).where(Document.org_id == org_id)
    if kind:
        stmt = stmt.where(Document.kind == kind)
    return list(await db.scalars(stmt.order_by(Document.created_at.desc())))


async def find(db: AsyncSession, org_id: str, *, sha256: str, kind: str) -> Document | None:
    """The registry row for one stored object of this tenant — None when the
    bytes were never vaulted under that kind (WO-AF: a statement digested
    before vaulting existed). Tenant-scoped, so a foreign sha is simply absent."""
    return await db.scalar(
        select(Document).where(
            Document.org_id == org_id, Document.sha256 == sha256, Document.kind == kind
        )
    )


async def vaulted(db: AsyncSession, org_id: str, *, kind: str, shas: set[str]) -> set[str]:
    """Which of `shas` have bytes on file under `kind` for this tenant — one
    query for a whole worklist, so a screen can offer a download only where
    one can be served."""
    if not shas:
        return set()
    rows = await db.scalars(
        select(Document.sha256).where(
            Document.org_id == org_id, Document.kind == kind, Document.sha256.in_(shas)
        )
    )
    return set(rows)
