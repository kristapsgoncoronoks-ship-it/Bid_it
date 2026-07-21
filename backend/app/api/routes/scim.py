"""SCIM 2.0 provisioning endpoints (ADR-0021). Authenticated by a per-connection
bearer token (NOT a user session); tenant-scoped to that connection's org."""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import DbSession
from app.core.tenant import apply_db_tenant, set_current_org
from app.models.sso import SsoConnection
from app.services import scim

router = APIRouter(prefix="/scim/v2", tags=["scim"])
_SCIM_CT = "application/scim+json"
_FILTER = re.compile(r'userName\s+eq\s+"([^"]+)"', re.IGNORECASE)


def _err(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, media_type=_SCIM_CT,
        content={"schemas": [scim.ERROR_SCHEMA], "detail": detail, "status": str(status_code)},
    )


def _json(content: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, media_type=_SCIM_CT, content=content)


async def require_scim(request: Request, db: DbSession) -> SsoConnection:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    conn = await scim.resolve_token(db, token)
    if conn is None:
        # Signal via exception the middleware won't wrap; the routes below catch
        # ScimError, but auth must short-circuit — raise a tagged error.
        raise scim.ScimError(status.HTTP_401_UNAUTHORIZED, "Invalid SCIM token")
    set_current_org(conn.org_id)      # scope RLS + tenant guard to this connection's org
    await apply_db_tenant(db)
    return conn


ScimConn = Annotated[SsoConnection, Depends(require_scim)]


@router.post("/Users")
async def create_user(request: Request, conn: ScimConn, db: DbSession):
    resource = await request.json()
    user = await scim.create_user(db, conn.org_id, resource, default_role=conn.default_role)
    return _json(scim.to_scim(user), status.HTTP_201_CREATED)


@router.get("/Users")
async def list_users(conn: ScimConn, db: DbSession, filter: str | None = None,
                     startIndex: int = 1, count: int = 100):
    m = _FILTER.search(filter) if filter else None
    email = m.group(1) if m else None
    rows, total = await scim.list_users(db, conn.org_id, email_filter=email,
                                        start_index=startIndex, count=count)
    return _json({
        "schemas": [scim.LIST_SCHEMA], "totalResults": total,
        "startIndex": startIndex, "itemsPerPage": len(rows),
        "Resources": [scim.to_scim(u) for u in rows],
    })


@router.get("/Users/{user_id}")
async def get_user(user_id: str, conn: ScimConn, db: DbSession):
    return _json(scim.to_scim(await scim.get_user(db, conn.org_id, user_id)))


@router.put("/Users/{user_id}")
async def replace_user(user_id: str, request: Request, conn: ScimConn, db: DbSession):
    resource = await request.json()
    return _json(scim.to_scim(await scim.replace_user(db, conn.org_id, user_id, resource)))


@router.patch("/Users/{user_id}")
async def patch_user(user_id: str, request: Request, conn: ScimConn, db: DbSession):
    body = await request.json()
    return _json(scim.to_scim(await scim.patch_user(db, conn.org_id, user_id, body)))


@router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, conn: ScimConn, db: DbSession):
    await scim.deactivate_user(db, conn.org_id, user_id)   # soft-delete (deactivate)
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
