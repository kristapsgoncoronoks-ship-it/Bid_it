"""Reusable authorization service — the single source of truth for *what a role
may do* (deny-by-default).

Design (see docs/security/authorization-policy-matrix.md):

- `Permission` is the capability vocabulary (verbs over resources). A route asks
  for a permission; it never inspects raw roles.
- `Role` is the eight business roles. `ROLE_PERMISSIONS` is the matrix — a role
  grants EXACTLY the listed permissions and nothing else (deny-by-default: an
  unlisted permission is denied).
- `business_role(user)` maps the CURRENTLY-STORED role onto a business role, so
  this layer works on today's accounts WITHOUT a schema change:
    owner→OWNER, admin→ADMINISTRATOR, user→EMPLOYEE, user_free→READ_ONLY.
  It is also forward-compatible: if the stored role is already one of the eight
  business-role values (after the role-model expansion), it is used directly.
- `is_platform_admin` (cross-tenant operator) is granted every permission;
  `is_expense_approver` (the existing per-user flag) additively grants
  EXPENSE_APPROVE, bridging today's approver concept onto the APPROVER role.

Tenant isolation is enforced separately (deps + the tenant guard + RLS); this
module answers "may THIS user perform THIS action", never "which org".
"""

from __future__ import annotations

import enum

from fastapi import HTTPException, status


class Permission(str, enum.Enum):
    # Received invoices
    INVOICE_READ = "invoice.read"
    INVOICE_WRITE = "invoice.write"
    INVOICE_DELETE = "invoice.delete"
    INVOICE_APPROVE = "invoice.approve"  # decide an approval step (Phase 08)
    # Expenses
    EXPENSE_READ = "expense.read"
    EXPENSE_WRITE = "expense.write"
    EXPENSE_APPROVE = "expense.approve"
    # Issued invoices (sales)
    ISSUED_READ = "issued.read"
    ISSUED_WRITE = "issued.write"
    ISSUED_SEND = "issued.send"
    # Analytics / reporting
    REPORT_READ = "report.read"
    # Accounting / ERP / e-invoice exports
    EXPORT_RUN = "export.run"
    # Audit log
    AUDIT_READ = "audit.read"
    # Org administration
    MEMBER_READ = "member.read"
    MEMBER_MANAGE = "member.manage"  # invite / remove / deactivate
    ROLE_ASSIGN = "role.assign"
    SETTINGS_MANAGE = "settings.manage"  # org settings, modules, issuer profile
    BILLING_MANAGE = "billing.manage"


ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)


class Role(str, enum.Enum):
    OWNER = "organization_owner"
    ADMINISTRATOR = "administrator"
    FINANCE_MANAGER = "finance_manager"
    ACCOUNTANT = "accountant"
    APPROVER = "approver"
    EMPLOYEE = "employee"
    AUDITOR = "auditor"
    READ_ONLY = "read_only"


_P = Permission

# The authorization matrix — deny-by-default (a role has ONLY what is listed).
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: ALL_PERMISSIONS,
    # Full business administration; billing stays owner-only.
    Role.ADMINISTRATOR: frozenset(ALL_PERMISSIONS - {_P.BILLING_MANAGE}),
    # Runs the finance function: full invoices/expenses/issuing + approve, send,
    # export, reporting, and read the audit log. No member/role/settings/billing.
    Role.FINANCE_MANAGER: frozenset(
        {
            _P.INVOICE_READ,
            _P.INVOICE_WRITE,
            _P.INVOICE_DELETE,
            _P.INVOICE_APPROVE,
            _P.EXPENSE_READ,
            _P.EXPENSE_WRITE,
            _P.EXPENSE_APPROVE,
            _P.ISSUED_READ,
            _P.ISSUED_WRITE,
            _P.ISSUED_SEND,
            _P.REPORT_READ,
            _P.EXPORT_RUN,
            _P.AUDIT_READ,
        }
    ),
    # Books the numbers: read/write invoices/expenses/issuing + export + report.
    # No approving, no sending, no administration.
    Role.ACCOUNTANT: frozenset(
        {
            _P.INVOICE_READ,
            _P.INVOICE_WRITE,
            _P.EXPENSE_READ,
            _P.EXPENSE_WRITE,
            _P.ISSUED_READ,
            _P.ISSUED_WRITE,
            _P.REPORT_READ,
            _P.EXPORT_RUN,
        }
    ),
    # Approves expenses AND supplier invoices; otherwise read-only on the money surfaces.
    Role.APPROVER: frozenset(
        {
            _P.EXPENSE_READ,
            _P.EXPENSE_APPROVE,
            _P.INVOICE_READ,
            _P.INVOICE_APPROVE,
            _P.REPORT_READ,
        }
    ),
    # Submits their own expenses; reads invoices.
    Role.EMPLOYEE: frozenset({_P.EXPENSE_READ, _P.EXPENSE_WRITE, _P.INVOICE_READ}),
    # Read-everything for assurance + read the audit trail + run exports.
    Role.AUDITOR: frozenset(
        {
            _P.INVOICE_READ,
            _P.EXPENSE_READ,
            _P.ISSUED_READ,
            _P.REPORT_READ,
            _P.AUDIT_READ,
            _P.EXPORT_RUN,
        }
    ),
    # Pure read of the money surfaces.
    Role.READ_ONLY: frozenset({_P.INVOICE_READ, _P.EXPENSE_READ, _P.ISSUED_READ, _P.REPORT_READ}),
}

# Today's stored 4-tier role → a business role (backward compatibility).
_LEGACY_ROLE: dict[str, Role] = {
    "owner": Role.OWNER,
    "admin": Role.ADMINISTRATOR,
    "user": Role.EMPLOYEE,
    "user_free": Role.READ_ONLY,
}


def business_role(user) -> Role:
    """Resolve a user's business role from the stored role. Forward-compatible:
    an already-expanded 8-role value is used directly; a legacy value is mapped;
    anything unrecognised falls back to the least privilege (READ_ONLY)."""
    raw = getattr(user, "role", None)
    if raw is None:
        return Role.READ_ONLY
    val = raw.value if hasattr(raw, "value") else str(raw)
    try:
        return Role(val)
    except ValueError:
        return _LEGACY_ROLE.get(val, Role.READ_ONLY)


def permissions_for(user) -> frozenset[Permission]:
    """The effective permission set for a user (deny-by-default)."""
    if getattr(user, "is_platform_admin", False):
        return ALL_PERMISSIONS  # cross-tenant operator
    perms = set(ROLE_PERMISSIONS[business_role(user)])
    if getattr(user, "is_expense_approver", False):
        perms.add(Permission.EXPENSE_APPROVE)  # bridges the existing approver flag
    return frozenset(perms)


def has(user, permission: Permission) -> bool:
    return permission in permissions_for(user)


def require(user, *permissions: Permission) -> None:
    """Enforce that the user holds EVERY given permission, else 403. This is the
    single choke point routes call — never an ad-hoc role comparison."""
    granted = permissions_for(user)
    missing = [p for p in permissions if p not in granted]
    if missing:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Insufficient permissions: " + ", ".join(p.value for p in missing),
        )


def matrix() -> dict[str, list[str]]:
    """The role→permission grid, JSON-friendly (for the admin UI and the doc)."""
    return {r.value: sorted(p.value for p in perms) for r, perms in ROLE_PERMISSIONS.items()}
