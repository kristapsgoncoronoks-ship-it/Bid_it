"""Role ranking + privilege helpers for the four per-company user groups.

Order (low → high): user_free < user < admin < owner.

  • user_free — non-paying; limited access, usage limits from the system matrix
  • user      — paying; usage limits from the system matrix
  • admin     — business administration WITHIN the company (the admin panel)
  • owner     — the company's primary user; full administration of THEIR company
                (user management + roles). NOT a system administrator.

Every role is scoped to the user's own company (tenant); none grants any
cross-company or system-wide power. Platform-operator access (reading/editing
across tenants, the global limits matrix) is the separate `is_platform_admin`
flag — never a company role. `is_platform_admin` outranks any company role.
"""

from __future__ import annotations

from app.models.user import UserRole

ROLE_RANK: dict[UserRole, int] = {
    UserRole.user_free: 0,
    UserRole.user: 1,
    UserRole.admin: 2,
    UserRole.owner: 3,
}

# Roles an owner may assign / that appear in the matrix, low → high.
ASSIGNABLE_ROLES: tuple[UserRole, ...] = (
    UserRole.user_free,
    UserRole.user,
    UserRole.admin,
    UserRole.owner,
)


def rank(user) -> int:
    # A platform operator outranks any company role (cross-tenant operator).
    if getattr(user, "is_platform_admin", False):
        return ROLE_RANK[UserRole.owner]
    return ROLE_RANK.get(user.role, 0)


def is_admin_or_above(user) -> bool:
    return rank(user) >= ROLE_RANK[UserRole.admin]


def is_owner(user) -> bool:
    """True for the company's top role (owner) — full administration of that
    company only. Not to be confused with a platform operator."""
    return rank(user) >= ROLE_RANK[UserRole.owner]
