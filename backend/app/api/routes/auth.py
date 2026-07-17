from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MeOut,
    RegisterRequest,
    Token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_for(user: User) -> Token:
    return Token(access_token=create_access_token(user.id, {"org": user.org_id}))


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: DbSession) -> AuthResponse:
    existing = await db.scalar(select(User).where(User.email == body.email.lower()))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    org = Organization(name=body.organization_name)
    db.add(org)
    await db.flush()  # assign org.id

    user = User(
        org_id=org.id,
        email=body.email.lower(),
        name=body.name,
        hashed_password=hash_password(body.password),
        role=UserRole.owner,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await db.refresh(org)

    return AuthResponse(token=_token_for(user), user=user, organization=org)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, db: DbSession) -> AuthResponse:
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

    org = await db.get(Organization, user.org_id)
    return AuthResponse(token=_token_for(user), user=user, organization=org)


@router.get("/me", response_model=MeOut)
async def me(current: CurrentUser, db: DbSession) -> MeOut:
    org = await db.get(Organization, current.org_id)
    return MeOut(user=current, organization=org)
