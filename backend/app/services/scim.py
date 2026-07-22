"""SCIM 2.0 user provisioning (ADR-0021).

The tenant's IdP (Okta / Entra) calls these to create / update / **deactivate**
users — the auto-offboarding story. We serve the standard `Users` resource over
our `User` model, tenant-scoped, authenticated by a per-connection bearer token
(sha256 stored). Deactivation is a soft `active=false` (never a hard delete) so
audit attribution + foreign keys survive.

Provable offline (it's a REST API we serve); the remaining real-IdP work is
paging/PATCH-dialect quirks (Okta vs Entra) — the ADR-0021 finish item.
"""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.sso import SsoConnection
from app.models.user import User, UserRole

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
_UNUSABLE_PASSWORD = "!scim-no-password"


class ScimError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> str:
    return "scim_" + secrets.token_urlsafe(32)


async def resolve_token(db: AsyncSession, token: str) -> SsoConnection | None:
    """Find the SCIM-enabled connection whose token matches (constant work)."""
    if not token:
        return None
    conn = await db.scalar(
        select(SsoConnection).where(SsoConnection.scim_token_hash == hash_token(token))
    )
    return conn if conn and conn.scim_enabled else None


async def set_token(db: AsyncSession, org_id: str) -> str:
    """Enable SCIM for the org's connection and mint a fresh token (returned once)."""
    conn = await db.scalar(select(SsoConnection).where(SsoConnection.org_id == org_id))
    if conn is None:
        raise ScimError(400, "Configure the SSO connection before enabling SCIM")
    token = generate_token()
    conn.scim_token_hash = hash_token(token)
    conn.scim_enabled = True
    await db.commit()
    return token


# --- resource mapping ------------------------------------------------------


def to_scim(user: User) -> dict:
    return {
        "schemas": [USER_SCHEMA],
        "id": user.id,
        "userName": user.email,
        "name": {"formatted": user.name},
        "displayName": user.name,
        "active": user.is_active,
        "emails": [{"value": user.email, "primary": True}],
        "meta": {"resourceType": "User"},
    }


def _name_from(resource: dict, email: str) -> str:
    n = resource.get("name") or {}
    formatted = n.get("formatted")
    if formatted:
        return formatted[:200]
    parts = [n.get("givenName"), n.get("familyName")]
    joined = " ".join(p for p in parts if p).strip()
    return (joined or resource.get("displayName") or email)[:200]


def _email_from(resource: dict) -> str | None:
    email = resource.get("userName")
    if not email:
        emails = resource.get("emails") or []
        primary = next((e for e in emails if e.get("primary")), None) or (
            emails[0] if emails else None
        )
        email = primary.get("value") if primary else None
    return email.strip().lower() if email else None


# --- CRUD ------------------------------------------------------------------


async def create_user(db: AsyncSession, org_id: str, resource: dict, *, default_role: str) -> User:
    email = _email_from(resource)
    if not email:
        raise ScimError(400, "userName (email) is required")
    existing = await db.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None:
        if existing.org_id != org_id:
            raise ScimError(409, "That user already exists in another workspace")
        # Idempotent-ish: reactivate + return the existing user.
        existing.is_active = resource.get("active", True)
        await db.commit()
        return existing
    role = default_role if default_role in UserRole.__members__ else UserRole.user.value
    user = User(
        org_id=org_id,
        email=email,
        name=_name_from(resource, email),
        hashed_password=hash_password(_UNUSABLE_PASSWORD),
        role=UserRole(role),
        is_active=resource.get("active", True),
    )
    db.add(user)
    await db.flush()
    # Dual-write the membership (Slice 6b).
    from app.services import memberships

    await memberships.ensure(
        db,
        org_id=org_id,
        user_id=user.id,
        role=UserRole(role),
        status="active" if user.is_active else "suspended",
    )
    await db.commit()
    await db.refresh(user)
    return user


async def get_user(db: AsyncSession, org_id: str, user_id: str) -> User:
    user = await db.scalar(select(User).where(User.org_id == org_id, User.id == user_id))
    if user is None:
        raise ScimError(404, "User not found")
    return user


async def list_users(
    db: AsyncSession, org_id: str, *, email_filter: str | None, start_index: int, count: int
) -> tuple[list[User], int]:
    where = [User.org_id == org_id]
    if email_filter:
        where.append(func.lower(User.email) == email_filter.lower())
    total = int(await db.scalar(select(func.count()).select_from(User).where(*where)) or 0)
    rows = list(
        await db.scalars(
            select(User)
            .where(*where)
            .order_by(User.created_at.asc())
            .offset(max(0, start_index - 1))
            .limit(count)
        )
    )
    return rows, total


async def replace_user(db: AsyncSession, org_id: str, user_id: str, resource: dict) -> User:
    user = await get_user(db, org_id, user_id)
    email = _email_from(resource)
    if email:
        user.email = email
    user.name = _name_from(resource, user.email)
    if "active" in resource:
        user.is_active = bool(resource["active"])
    await db.commit()
    return user


async def patch_user(db: AsyncSession, org_id: str, user_id: str, body: dict) -> User:
    """Apply a SCIM PATCH. We support the common shape: replace `active` (the
    deactivation path) and `name`/`displayName`. Other ops are accepted no-ops."""
    user = await get_user(db, org_id, user_id)
    for op in body.get("Operations", []):
        if (op.get("op") or "").lower() not in ("replace", "add"):
            continue
        path = (op.get("path") or "").lower()
        value = op.get("value")
        if path == "active":
            user.is_active = _as_bool(value)
        elif isinstance(value, dict) and "active" in value:  # pathless replace
            user.is_active = _as_bool(value["active"])
        elif path in ("name.formatted", "displayname"):
            user.name = str(value)[:200]
    await db.commit()
    return user


async def deactivate_user(db: AsyncSession, org_id: str, user_id: str) -> None:
    user = await get_user(db, org_id, user_id)
    user.is_active = False
    await db.commit()


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")
