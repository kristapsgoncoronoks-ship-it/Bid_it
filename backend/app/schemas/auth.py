from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class RegisterRequest(BaseModel):
    organization_name: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    plan: str = "trial"
    status: str = "active"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    name: str
    role: UserRole
    org_id: str
    is_platform_admin: bool = False


class MeOut(BaseModel):
    user: UserOut
    organization: OrganizationOut


class AuthResponse(BaseModel):
    token: Token
    user: UserOut
    organization: OrganizationOut
