"""Document registry API (Slice 5d) — the metadata catalog of stored originals.

Read-only, admin-gated (it spans every user's uploads in the tenant). Metadata
only; the bytes are served by each owner's existing download route.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.schemas.document import DocumentOut
from app.services import document_registry

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=list[DocumentOut])
async def list_documents(current: CurrentUser, db: DbSession, kind: str | None = None):
    authz.require(current, authz.Permission.SETTINGS_MANAGE)
    return await document_registry.list_for_org(db, current.org_id, kind=kind)
