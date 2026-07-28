"""Role ranking + privilege helpers for the original four per-company user
groups (the LEGACY administration ladder), plus rank entries for the four
business roles A1.5 made directly assignable.

Order (low → high) of the legacy ladder: user_free < user < admin < owner.

  • user_free — read-only-tier permission role
  • user      — standard permission role
  • admin     — business administration WITHIN the company (the admin panel)
  • owner     — the company's primary user; full administration of THEIR company
                (user management + roles). NOT a system administrator.

Every role is scoped to the user's own company (tenant); none grants any
cross-company or system-wide power. Platform-operator access (reading/editing
across tenants, the global limits matrix) is the separate `is_platform_admin`
flag — never a company role. `is_platform_admin` outranks any company role.

This ladder is DISTINCT from (but keyed off the same `UserRole` storage as) the
8-role business-permission matrix in `app.core.authz` — `ROLE_RANK`/`rank()`
here only drive the coarse `is_admin_or_above`/`is_owner` defense-in-depth
checks; the actual authorization decision is always
`authz.permissions_for`/`authz.require`, never this module. The four
newly-reachable business roles (`finance_manager`/`accountant`/`approver`/
`auditor`) rank alongside `user` (non-admin): none of them holds
`SETTINGS_MANAGE` in the authz matrix, so none should ever read as
`is_admin_or_above()` here either — that stays reserved for `admin`/`owner`.

WO-47: usage QUOTAS are no longer part of this ladder at all — they key off
the org's subscription plan (`app.services.access`/`app.services.plans`),
org-wide, shared by every member regardless of rank. `ASSIGNABLE_ROLES` below
is the role-assignment vocabulary only; it no longer doubles as the quota
matrix's row set (that is now `app.services.plans.PLANS`).
"""

from __future__ import annotations

from app.models.user import UserRole

ROLE_RANK: dict[UserRole, int] = {
    UserRole.user_free: 0,
    UserRole.user: 1,
    UserRole.admin: 2,
    UserRole.owner: 3,
    # The four business roles A1.5 made directly assignable — none holds
    # SETTINGS_MANAGE in authz.ROLE_PERMISSIONS, so they rank as non-admin,
    # same tier as `user`. See the module docstring above.
    UserRole.finance_manager: 1,
    UserRole.accountant: 1,
    UserRole.approver: 1,
    UserRole.auditor: 1,
}

# Roles a member may be assigned, low → high within the legacy ladder, then
# the four newly-reachable business roles in the same order `authz.Role`
# declares them. (WO-47: no longer doubles as the quota matrix's row set.)
ASSIGNABLE_ROLES: tuple[UserRole, ...] = (
    UserRole.user_free,
    UserRole.user,
    UserRole.admin,
    UserRole.owner,
    UserRole.finance_manager,
    UserRole.accountant,
    UserRole.approver,
    UserRole.auditor,
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
