from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.invitation import Invitation
from app.models.organization import Organization
from app.models.sso import SsoConnection
from app.models.user import User, UserRole
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MeOut,
    RegisterRequest,
    Token,
)
from app.schemas.tenancy import AcceptInvite, InvitePreview
from app.services import audit, oidc, sso_config, team

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("invoiceiq.auth")


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
        role=UserRole.owner,   # the first user is the OWNER of THIS company only
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


# --- SSO (OIDC) login (ADR-0021) -------------------------------------------

def _sso_error_redirect() -> RedirectResponse:
    return RedirectResponse(f"{settings.sso_error_url}?sso_error=1", status_code=status.HTTP_302_FOUND)


@router.get("/sso/{slug}/authorize", include_in_schema=False)
async def sso_authorize(slug: str, db: DbSession):
    """Begin OIDC login: redirect the browser to the tenant's IdP with PKCE +
    a signed, stateless `state`."""
    conn = await sso_config.get_by_slug(db, slug)
    if conn is None or not conn.enabled or conn.protocol != "oidc":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO is not enabled for this workspace")
    try:
        disco = await oidc.discover(conn.issuer or "")
        verifier, challenge = oidc.pkce_pair()
        nonce = secrets.token_urlsafe(16)
        state = oidc.sign_state(conn.id, nonce, verifier)
        url = oidc.build_authorize_url(
            disco["authorization_endpoint"], client_id=conn.client_id or "",
            redirect_uri=settings.sso_redirect_uri, state=state, nonce=nonce, code_challenge=challenge,
        )
    except Exception as exc:  # noqa: BLE001 - IdP unreachable / misconfigured
        log.warning("SSO authorize failed for %s: %s", slug, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Identity provider is unreachable")
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/sso/callback", include_in_schema=False)
async def sso_callback(db: DbSession, code: str | None = None, state: str | None = None,
                       error: str | None = None):
    """OIDC redirect back: validate + JIT-provision, then bounce to the SPA with
    our internal token in the URL fragment. Any failure → the SPA login page."""
    if error or not code or not state:
        return _sso_error_redirect()
    try:
        st = oidc.read_state(state)
        conn = await db.get(SsoConnection, st["conn"])
        if conn is None or not conn.enabled:
            raise oidc.SsoError("connection unavailable")
        user, org = await oidc.finish_login(db, conn, code=code, nonce=st["nonce"], code_verifier=st["cv"])
    except oidc.SsoError as exc:
        log.warning("SSO callback rejected: %s", exc)
        return _sso_error_redirect()
    except Exception:  # noqa: BLE001 - never 500 into a browser redirect flow
        log.exception("SSO callback error")
        return _sso_error_redirect()
    token = create_access_token(user.id, {"org": org.id})
    return RedirectResponse(f"{settings.sso_post_login_url}#access_token={token}",
                            status_code=status.HTTP_302_FOUND)


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
