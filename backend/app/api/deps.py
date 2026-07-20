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

from app.core.database import get_session
from app.core.security import decode_access_token
from app.core.tenant import set_current_actor, set_current_org
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if creds is None or not creds.credentials:
        raise _CREDENTIALS_EXC
    payload = decode_access_token(creds.credentials)
    if not payload or "sub" not in payload:
        raise _CREDENTIALS_EXC
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise _CREDENTIALS_EXC
    # Activate defence-in-depth tenant scoping + audit attribution for this request.
    set_current_org(user.org_id)
    set_current_actor(user.id, user.email)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
