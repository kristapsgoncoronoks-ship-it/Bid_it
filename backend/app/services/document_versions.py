"""Supersession-chain service (Slice 5g).

Appends one immutable row per upload for a single-file slot `(owner_type,
owner_id)` and keeps exactly one row flagged `is_current`. The caller updates the
owner's `*_sha256` cache and commits — this service only stages the history rows
in that same session (does NOT commit), so the chain and the cache move together.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_version import DocumentVersion


async def record(
    db: AsyncSession,
    org_id: str,
    owner_type: str,
    owner_id: str,
    *,
    sha256: str,
    size: int,
    mime: str | None,
    filename: str | None,
    uploaded_by: str | None,
) -> DocumentVersion:
    """Append a new version to the slot and mark it current (demoting the prior
    current row). Does NOT commit — the caller commits with the owner cache."""
    last = await db.scalar(
        select(func.max(DocumentVersion.version)).where(
            DocumentVersion.org_id == org_id,
            DocumentVersion.owner_type == owner_type,
            DocumentVersion.owner_id == owner_id,
        )
    )
    if last:
        await db.execute(
            update(DocumentVersion)
            .where(
                DocumentVersion.org_id == org_id,
                DocumentVersion.owner_type == owner_type,
                DocumentVersion.owner_id == owner_id,
                DocumentVersion.is_current.is_(True),
            )
            .values(is_current=False)
        )
    row = DocumentVersion(
        org_id=org_id,
        owner_type=owner_type,
        owner_id=owner_id,
        version=(last or 0) + 1,
        is_current=True,
        sha256=sha256,
        size=size,
        mime=mime,
        filename=filename,
        uploaded_by=uploaded_by,
    )
    db.add(row)
    await db.flush()
    return row


async def history(
    db: AsyncSession, org_id: str, owner_type: str, owner_id: str
) -> list[DocumentVersion]:
    """The slot's versions, newest first."""
    return list(
        await db.scalars(
            select(DocumentVersion)
            .where(
                DocumentVersion.org_id == org_id,
                DocumentVersion.owner_type == owner_type,
                DocumentVersion.owner_id == owner_id,
            )
            .order_by(DocumentVersion.version.desc())
        )
    )
