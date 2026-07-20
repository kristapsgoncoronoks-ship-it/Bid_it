from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
from app.models.invitation import Invitation
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MeOut,
    RegisterRequest,
    Token,
)
from app.schemas.tenancy import AcceptInvite, InvitePreview
from app.services import audit, team

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
        role=UserRole.sysadmin,   # the first user of a new workspace owns it
        is_expense_approver=True,  # the owner is an expense approver by default
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await db.refresh(org)

    await audit.record(db, audit.A.REGISTER, org_id=org.id, actor=(user.id, user.email),
                       target_type="organization", target_id=org.id)
    await db.commit()
    return AuthResponse(token=_token_for(user), user=user, organization=org)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, db: DbSession) -> AuthResponse:
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

    org = await db.get(Organization, user.org_id)
    if org.status != "active" and not user.is_platform_admin:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"Workspace is {org.status}. Contact support.")
    await audit.record(db, audit.A.LOGIN, org_id=user.org_id, actor=(user.id, user.email))
    await db.commit()
    return AuthResponse(token=_token_for(user), user=user, organization=org)


@router.get("/me", response_model=MeOut)
async def me(current: CurrentUser, db: DbSession) -> MeOut:
    org = await db.get(Organization, current.org_id)
    return MeOut(user=current, organization=org)


@router.get("/invite/{token}", response_model=InvitePreview)
async def preview_invite(token: str, db: DbSession) -> InvitePreview:
    inv = await db.scalar(
        select(Invitation).where(Invitation.token == token, Invitation.accepted.is_(False))
    )
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found or already used")
    org = await db.get(Organization, inv.org_id)
    return InvitePreview(email=inv.email, organization_name=org.name, role=inv.role)


@router.post("/accept-invite", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def accept_invite(body: AcceptInvite, db: DbSession) -> AuthResponse:
    result = await team.accept_invitation(db, body.token, body.name, body.password)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found or already used")
    user, org_id = result
    org = await db.get(Organization, org_id)
    return AuthResponse(token=_token_for(user), user=user, organization=org)
