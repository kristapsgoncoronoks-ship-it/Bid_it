"""SSO connection configuration (admin CRUD) — ADR-0021."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sso import SsoConnection

_EDITABLE = (
    "slug", "protocol", "enabled", "issuer", "client_id", "client_secret",
    "allowed_domain", "jit_enabled", "default_role", "saml_metadata_url",
    "saml_sso_url", "saml_idp_entity_id", "saml_idp_cert",
)


async def get_connection(db: AsyncSession, org_id: str) -> SsoConnection | None:
    return await db.scalar(select(SsoConnection).where(SsoConnection.org_id == org_id))


async def get_by_slug(db: AsyncSession, slug: str) -> SsoConnection | None:
    """Unscoped lookup for the public login route (no tenant context yet)."""
    return await db.scalar(select(SsoConnection).where(func.lower(SsoConnection.slug) == slug.lower()))


async def upsert_connection(db: AsyncSession, org_id: str, fields: dict) -> SsoConnection:
    conn = await get_connection(db, org_id)
    data = {k: v for k, v in fields.items() if k in _EDITABLE and v is not None}
    if conn is None:
        conn = SsoConnection(org_id=org_id, **data)
        db.add(conn)
    else:
        for k, v in data.items():
            setattr(conn, k, v)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError("that slug is already taken") from exc
    await db.refresh(conn)
    return conn


async def delete_connection(db: AsyncSession, org_id: str) -> bool:
    conn = await get_connection(db, org_id)
    if conn is None:
        return False
    await db.delete(conn)
    await db.commit()
    return True
