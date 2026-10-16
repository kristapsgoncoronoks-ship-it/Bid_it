"""Immutable request identity; never write tenant-specific roles onto a User."""

from dataclasses import dataclass

from app.models.membership import Membership
from app.models.user import User, UserRole


@dataclass(frozen=True)
class Principal:
    id: str
    org_id: str
    email: str
    name: str
    role: UserRole
    is_expense_approver: bool
    is_platform_admin: bool
    is_active: bool
    email_verified: bool
    iban: str | None
    bic: str | None


def principal(user: User, membership: Membership) -> Principal:
    return Principal(
        id=user.id,
        org_id=membership.org_id,
        email=user.email,
        name=user.name,
        role=membership.role,
        is_expense_approver=membership.is_expense_approver,
        is_platform_admin=user.is_platform_admin,
        is_active=user.is_active and membership.status == "active",
        email_verified=user.email_verified,
        iban=user.iban,
        bic=user.bic,
    )
