"""Role ranking + privilege helpers for the four user groups.

Order (low → high): user_free < user < admin < sysadmin.

  • user_free — non-paying; limited access, usage limits from the system matrix
  • user      — paying; usage limits from the system matrix
  • admin      — access to the admin panel (business administration)
  • sysadmin   — everything, including user-rights management and the matrix

`is_platform_admin` (cross-tenant operator) is always treated as sysadmin-level.
Anything that takes a `user` here only needs `.role` and `.is_platform_admin`.
"""
from __future__ import annotations

from app.models.user import UserRole

ROLE_RANK: dict[UserRole, int] = {
    UserRole.user_free: 0,
    UserRole.user: 1,
    UserRole.admin: 2,
    UserRole.sysadmin: 3,
}

# Roles a sysadmin may assign / that appear in the matrix, low → high.
ASSIGNABLE_ROLES: tuple[UserRole, ...] = (
    UserRole.user_free, UserRole.user, UserRole.admin, UserRole.sysadmin,
)


def rank(user) -> int:
    if getattr(user, "is_platform_admin", False):
        return ROLE_RANK[UserRole.sysadmin]
    return ROLE_RANK.get(user.role, 0)


def is_admin_or_above(user) -> bool:
    return rank(user) >= ROLE_RANK[UserRole.admin]


def is_sysadmin(user) -> bool:
    return rank(user) >= ROLE_RANK[UserRole.sysadmin]
