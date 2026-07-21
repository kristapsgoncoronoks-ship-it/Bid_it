"""SSO connection administration (ADR-0021). Admin-gated, tenant-scoped. The
public login endpoints live on the auth router (`/auth/sso/...`)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.roles import is_admin_or_above
from app.schemas.sso import SsoConnectionOut, SsoConnectionUpdate
from app.services import sso_config

router = APIRouter(prefix="/sso", tags=["sso"])


def _require_admin(current) -> None:
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can manage SSO")


def _out(conn) -> SsoConnectionOut:
    login = f"{settings.api_public_base_url.rstrip('/')}{settings.api_v1_prefix}/auth/sso/{conn.slug}/authorize"
    return SsoConnectionOut(
        id=conn.id, slug=conn.slug, protocol=conn.protocol, enabled=conn.enabled,
        issuer=conn.issuer, client_id=conn.client_id, allowed_domain=conn.allowed_domain,
        jit_enabled=conn.jit_enabled, default_role=conn.default_role,
        saml_metadata_url=conn.saml_metadata_url,
        has_client_secret=bool(conn.client_secret), login_url=login,
    )


@router.get("/connection", response_model=SsoConnectionOut | None)
async def get_connection(current: CurrentUser, db: DbSession):
    _require_admin(current)
    conn = await sso_config.get_connection(db, current.org_id)
    return _out(conn) if conn else None


@router.put("/connection", response_model=SsoConnectionOut)
async def put_connection(body: SsoConnectionUpdate, current: CurrentUser, db: DbSession):
    _require_admin(current)
    fields = body.model_dump(exclude_none=True)
    existing = await sso_config.get_connection(db, current.org_id)
    if existing is None and not fields.get("slug"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A slug is required to create a connection")
    try:
        conn = await sso_config.upsert_connection(db, current.org_id, fields)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _out(conn)


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(current: CurrentUser, db: DbSession):
    _require_admin(current)
    await sso_config.delete_connection(db, current.org_id)
