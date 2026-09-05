from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class RegisterRequest(BaseModel):
    organization_name: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=72)


class BankDetailsIn(BaseModel):
    iban: str | None = Field(default=None, max_length=34)
    bic: str | None = Field(default=None, max_length=11)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SessionOut(BaseModel):
    """One active session for the "your sessions" list."""

    id: str
    created_at: datetime
    last_seen_at: datetime | None = None
    user_agent: str | None = None
    ip: str | None = None
    current: bool = False


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    plan: str = "trial"
    status: str = "active"
    region: str = "eu"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    name: str
    role: UserRole
    org_id: str
    is_platform_admin: bool = False
    is_expense_approver: bool = False
    email_verified: bool = False
    iban: str | None = None
    bic: str | None = None


class MeOut(BaseModel):
    user: UserOut
    organization: OrganizationOut
    # PROD-003 (audit 2026-09-05): the caller's EFFECTIVE permissions
    # (`authz.permissions_for`), so the SPA draws its navigation from what the
    # API will actually serve this role instead of from a 4-tier ladder that
    # disagreed with the 8-role matrix. Advisory for rendering only — every
    # route still enforces its own permission structurally.
    permissions: list[str] = []


class AuthResponse(BaseModel):
    token: Token
    user: UserOut
    organization: OrganizationOut
    permissions: list[str] = []
