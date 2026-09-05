"""SSO connection administration (ADR-0021). Admin-gated, tenant-scoped. The
public login endpoints live on the auth router (`/auth/sso/...`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.core.config import settings
from app.core.roles import idp_assignable_role_values
from app.schemas.sso import ScimTokenOut, SsoConnectionOut, SsoConnectionUpdate
from app.services import scim, sso_config

# Structural authorization (ADR-0024): SSO/SCIM connection administration is
# org configuration — router-level SETTINGS_MANAGE (the public login endpoints
# live on the auth router and stay in PUBLIC_ROUTES).
router = APIRouter(
    prefix="/sso",
    tags=["sso"],
    dependencies=[Depends(require_perm(authz.Permission.SETTINGS_MANAGE))],
)


def _out(conn) -> SsoConnectionOut:
    import json

    base = f"{settings.api_public_base_url.rstrip('/')}{settings.api_v1_prefix}"
    try:
        mappings = json.loads(conn.role_mappings) if conn.role_mappings else {}
    except (ValueError, TypeError):
        mappings = {}
    return SsoConnectionOut(
        id=conn.id,
        slug=conn.slug,
        protocol=conn.protocol,
        enabled=conn.enabled,
        issuer=conn.issuer,
        client_id=conn.client_id,
        allowed_domain=conn.allowed_domain,
        jit_enabled=conn.jit_enabled,
        default_role=conn.default_role,
        groups_claim=conn.groups_claim,
        role_mappings=mappings,
        role_sync=conn.role_sync,
        assignable_roles=list(idp_assignable_role_values()),
        saml_metadata_url=conn.saml_metadata_url,
        has_client_secret=bool(conn.client_secret),
        scim_enabled=conn.scim_enabled,
        login_url=f"{base}/auth/sso/{conn.slug}/authorize",
        scim_base_url=f"{base}/scim/v2",
    )


@router.get("/connection", response_model=SsoConnectionOut | None)
async def get_connection(current: CurrentUser, db: DbSession):
    conn = await sso_config.get_connection(db, current.org_id)
    return _out(conn) if conn else None


@router.get("/assignable-roles", response_model=list[str])
async def assignable_roles(current: CurrentUser):
    """The roles an identity provider may assign (WO-AE) — served so the
    settings screen's selects come from the server, including before the
    first connection is saved (when `GET /connection` is `null`)."""
    return list(idp_assignable_role_values())


@router.put("/connection", response_model=SsoConnectionOut)
async def put_connection(body: SsoConnectionUpdate, current: CurrentUser, db: DbSession):
    fields = body.model_dump(exclude_none=True)
    if "role_mappings" in fields:  # dict → JSON string for the Text column
        import json

        fields["role_mappings"] = json.dumps(fields["role_mappings"])
    existing = await sso_config.get_connection(db, current.org_id)
    if existing is None and not fields.get("slug"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "A slug is required to create a connection"
        )
    try:
        conn = await sso_config.upsert_connection(db, current.org_id, fields)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _out(conn)


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(current: CurrentUser, db: DbSession):
    await sso_config.delete_connection(db, current.org_id)


@router.post("/scim/token", response_model=ScimTokenOut)
async def rotate_scim_token(current: CurrentUser, db: DbSession):
    """Enable SCIM and mint a fresh provisioning token (shown ONCE). Regenerating
    invalidates the previous token."""
    try:
        token = await scim.set_token(db, current.org_id)
    except scim.ScimError as exc:
        raise HTTPException(exc.status, exc.detail)
    return ScimTokenOut(token=token)
