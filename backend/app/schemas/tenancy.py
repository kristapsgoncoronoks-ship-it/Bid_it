from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


# --- team ---
class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class MemberUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class InviteCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.member


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    role: UserRole
    token: str
    accepted: bool
    created_at: datetime


class AcceptInvite(BaseModel):
    token: str
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=128)


class InvitePreview(BaseModel):
    email: str
    organization_name: str
    role: UserRole


# --- billing ---
class PlanOut(BaseModel):
    key: str
    name: str
    seats: int
    price_eur: int | None
    modules: list[str]
    trial: bool


class BillingOut(BaseModel):
    plan: PlanOut
    status: str
    seats_used: int
    seats_limit: int
    available_plans: list[PlanOut]


class PlanChange(BaseModel):
    plan: str


# --- platform operator ---
class TenantOut(BaseModel):
    id: str
    name: str
    plan: str
    status: str
    seats_used: int
    created_at: datetime


class TenantUpdate(BaseModel):
    status: str | None = None
    plan: str | None = None
