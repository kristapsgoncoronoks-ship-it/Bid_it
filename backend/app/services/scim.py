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

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import unusable_password_hash
from app.models.membership import Membership
from app.models.sso import SsoConnection
from app.models.user import User, UserRole

# B1.5: SCIM resolves "who belongs to this workspace" through MEMBERSHIPS, never
# through `users.org_id` (that column is only the active-org pointer — a member
# currently switched into another org must still be provisionable/offboardable
# by THIS org's IdP). Membership existence, not status: a suspended member is
# still listed (as active=false) so the IdP sees the truth.


def _member_join(org_id: str):
    return and_(Membership.user_id == User.id, Membership.org_id == org_id)


USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


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
    from app.services import memberships

    # An existing MEMBER of this org (whatever their active org) → idempotent-ish
    # reactivate. Looked up via the membership join: a scoped session neither can
    # nor should read a foreign workspace's user row.
    existing = await db.scalar(
        select(User).join(Membership, _member_join(org_id)).where(func.lower(User.email) == email)
    )
    if existing is not None:
        existing.is_active = resource.get("active", True)
        await memberships.set_status(
            db, org_id, existing.id, "active" if existing.is_active else "suspended"
        )
        await db.commit()
        return existing
    role = default_role if default_role in UserRole.__members__ else UserRole.user.value
    user = User(
        org_id=org_id,
        email=email,
        name=_name_from(resource, email),
        # Provisioned users have no password (SEC-001): the sentinel, never a hash.
        hashed_password=unusable_password_hash(),
        role=UserRole(role),
        is_active=resource.get("active", True),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        # The email exists globally but has no membership here: the tenant-scoped
        # session must not (and cannot) see that row, so the DB's unique-email
        # constraint is the conflict detector. Fail CLOSED with the SCIM 409.
        await db.rollback()
        raise ScimError(409, "That user already exists in another workspace") from None
    # Write the authoritative membership (B1.5); `users.org_id` above is only the
    # new account's initial active-org pointer.
    await memberships.ensure(
        db,
        org_id=org_id,
        user_id=user.id,
        role=UserRole(role),
        status="active" if user.is_active else "suspended",
        email=user.email,
        name=user.name,
    )
    await db.commit()
    await db.refresh(user)
    return user


async def get_user(db: AsyncSession, org_id: str, user_id: str) -> User:
    user = await db.scalar(
        select(User).join(Membership, _member_join(org_id)).where(User.id == user_id)
    )
    if user is None:
        raise ScimError(404, "User not found")  # opaque: non-member ≡ nonexistent
    return user


async def list_users(
    db: AsyncSession, org_id: str, *, email_filter: str | None, start_index: int, count: int
) -> tuple[list[User], int]:
    where = []
    if email_filter:
        where.append(func.lower(User.email) == email_filter.lower())
    total = int(
        await db.scalar(
            select(func.count())
            .select_from(User)
            .join(Membership, _member_join(org_id))
            .where(*where)
        )
        or 0
    )
    rows = list(
        await db.scalars(
            select(User)
            .join(Membership, _member_join(org_id))
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
    from app.services import memberships

    await memberships.sync_identity(db, user_id, email=user.email, name=user.name)
    await memberships.set_status(db, org_id, user_id, "active" if user.is_active else "suspended")
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
    from app.services import memberships

    await memberships.sync_identity(db, user_id, email=user.email, name=user.name)
    await memberships.set_status(db, org_id, user_id, "active" if user.is_active else "suspended")
    await db.commit()
    return user


async def deactivate_user(db: AsyncSession, org_id: str, user_id: str) -> None:
    user = await get_user(db, org_id, user_id)
    user.is_active = False
    from app.services import memberships

    await memberships.set_status(db, org_id, user_id, "suspended")
    await db.commit()


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")
