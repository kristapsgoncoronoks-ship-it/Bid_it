"""Shared request dependencies: DB session + authenticated, tenant-scoped user.

`get_current_user` is the single choke point that turns a bearer token into a
`User`. Everything downstream reads `user.org_id`, so no endpoint can
accidentally serve another tenant's data.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import authz, residency
from app.core.config import settings
from app.core.database import get_session
from app.core.security import decode_access_token
from app.core.tenant import apply_db_tenant, set_current_actor, set_current_org
from app.models.organization import Organization
from app.models.session import Session
from app.models.user import User
from app.services import memberships, sessions

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _authenticate(
    db: AsyncSession, creds: HTTPAuthorizationCredentials | None
) -> tuple[User, Session]:
    """Validate the bearer token → (user, live session), WITHOUT setting tenant
    scope. Shared by the scoped and unscoped dependencies. Raises 401."""
    if creds is None or not creds.credentials:
        raise _CREDENTIALS_EXC
    payload = decode_access_token(creds.credentials)
    if not payload or "sub" not in payload:
        raise _CREDENTIALS_EXC
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise _CREDENTIALS_EXC
    # Session revocation (Slice 4): the token's `jti` must map to a live session.
    # A missing jti (legacy token) or a revoked/expired session is a hard 401.
    session = await sessions.active(db, payload.get("jti"))
    if session is None:
        raise _CREDENTIALS_EXC
    return user, session


async def get_current_user(
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    user, session = await _authenticate(db, creds)
    # Activate defence-in-depth tenant scoping + audit attribution for this request.
    # The ACTIVE org is `user.org_id` (repointed by org-switching); everything
    # downstream scopes to it.
    set_current_org(user.org_id)
    set_current_actor(user.id, user.email)
    # Active-membership enforcement (Slice 6d): the user must hold a LIVE
    # membership in their active org — a suspended/removed membership is a hard
    # 401 even if the global account is still active. Scoped, so it reads the
    # active org's membership.
    membership = await memberships.get(db, user.org_id, user.id)
    if membership is None or membership.status != "active":
        raise _CREDENTIALS_EXC
    await sessions.touch(db, session)
    # Pin the Postgres RLS GUC for this request's current transaction (no-op on
    # SQLite / when unscoped). The event hook re-applies it on later transactions.
    await apply_db_tenant(db)
    # Data-residency backstop: refuse a tenant pinned to another region (no-op
    # unless enforcement is on → single-region deployments skip the org fetch).
    if settings.enforce_region_pinning:
        org = await db.get(Organization, user.org_id)
        residency.assert_region(org)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


class PermissionDependency:
    """A declared, introspectable permission gate (ADR-0024).

    Built by `require_perm(...)` and attached structurally to a router or route:

        APIRouter(dependencies=[Depends(require_perm(Permission.X))])

    Enforcement reuses `authz.require` (which reuses `authz.permissions_for` —
    the SINGLE resolver; nothing is duplicated here), so a denial is the same
    403 wire shape as the historical in-handler calls. The declared tuple is
    carried under `authz.PERMISSIONS_ATTR` so the CI coverage test
    (tests/test_authz_coverage.py) can enumerate every route's declaration
    without executing anything.

    Fail-closed by construction: the gate depends on `get_current_user`, so an
    unauthenticated request is a 401 before any permission logic runs, and a
    missing permission is a hard 403 — there is no anonymous or partial pass.
    """

    def __init__(self, permissions: tuple[authz.Permission, ...]) -> None:
        if not permissions:
            raise ValueError("require_perm needs at least one Permission")
        self.permissions = permissions
        setattr(self, authz.PERMISSIONS_ATTR, permissions)
        # A readable name for debugging / dependency-cache keys.
        self.__name__ = "require_perm[" + ",".join(p.value for p in permissions) + "]"

    async def __call__(self, current: CurrentUser) -> None:
        authz.require(current, *self.permissions)


def require_perm(*permissions: authz.Permission) -> PermissionDependency:
    """FastAPI dependency factory: enforce EVERY given permission on the current
    user. Used as `APIRouter(dependencies=[Depends(require_perm(Permission.X))])`
    with per-route overrides for stricter verbs. Carries `.permissions` (and
    `authz.PERMISSIONS_ATTR`) so tests/CI can introspect what a route declares."""
    return PermissionDependency(permissions)


async def get_current_org(current: CurrentUser, db: DbSession) -> Organization:
    """The caller's active organization, guaranteed non-None. `current.org_id` is
    a live FK for any valid session, so a missing row means the account is in an
    inconsistent state → hard 401."""
    org = await db.get(Organization, current.org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Organization not found",
        )
    return org


CurrentOrg = Annotated[Organization, Depends(get_current_org)]


async def get_current_user_unscoped(
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Authenticated but deliberately NOT tenant-scoped — `current_org` stays
    None so a CROSS-ORG endpoint (list my organizations, switch org) can see all
    of the caller's memberships. Such endpoints MUST filter by `user_id`
    themselves; they never issue an unfiltered tenant query."""
    user, session = await _authenticate(db, creds)
    set_current_actor(user.id, user.email)
    await sessions.touch(db, session)
    return user


CurrentUserUnscoped = Annotated[User, Depends(get_current_user_unscoped)]


async def get_current_session(
    current: CurrentUser,
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Session:
    """The caller's own session row (already validated by `get_current_user`).
    Used by logout / session-management endpoints that act on the current jti."""
    _, session = await _authenticate(db, creds)
    return session


CurrentSession = Annotated[Session, Depends(get_current_session)]


async def get_current_session_unscoped(
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Session:
    """The caller's session WITHOUT tenant scope (for the cross-org switch)."""
    user, session = await _authenticate(db, creds)
    set_current_actor(user.id, user.email)
    return session


CurrentSessionUnscoped = Annotated[Session, Depends(get_current_session_unscoped)]
