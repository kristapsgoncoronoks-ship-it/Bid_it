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
    is_expense_approver: bool = False
    created_at: datetime


class MemberUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    is_expense_approver: bool | None = None


class InviteCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.user


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    role: UserRole
    token: str
    accepted: bool
    created_at: datetime
    expires_at: datetime | None = None


class AcceptInvite(BaseModel):
    token: str
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=72)


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
    # WO-AD: False for a PRICED plan the active provider cannot sell (Stripe with
    # no price id configured). The SPA must not offer a checkout that can only
    # fail, and the server is the one that knows.
    purchasable: bool = True
    # WO-AD (DECISIONS §1.B): retention rides the ladder — a plan attribute.
    archive_retention_years: int = 3


class BillingOut(BaseModel):
    plan: PlanOut
    status: str
    seats_used: int
    seats_limit: int
    available_plans: list[PlanOut]
    # True when a real payment provider is connected → the UI routes paid changes
    # through the provider's hosted flow instead of the in-app switch.
    billing_enabled: bool = False
    billing_provider: str = "none"  # stripe | everypay | none
    has_subscription: bool = False


class PlanChange(BaseModel):
    plan: str


class CheckoutStart(BaseModel):
    plan: str


class CheckoutOut(BaseModel):
    url: str


class PortalOut(BaseModel):
    url: str


# --- platform operator ---
class TenantOut(BaseModel):
    id: str
    name: str
    plan: str
    status: str
    seats_used: int
    created_at: datetime
    # The paid archive-retention extension: None = the included tier.
    archive_retention_years: int | None = None


class TenantUpdate(BaseModel):
    status: str | None = None
    plan: str | None = None
    # Grant (or clear, with 0) the paid archive-retention extension. Operator-
    # only, like everything on this schema; the commercial step happens
    # out-of-band until billing is wired.
    archive_retention_years: int | None = Field(default=None, ge=0, le=30)
