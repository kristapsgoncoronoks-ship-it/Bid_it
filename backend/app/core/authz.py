"""Reusable authorization service — the single source of truth for *what a role
may do* (deny-by-default).

Design (see docs/security/authorization-policy-matrix.md):

- `Permission` is the capability vocabulary (verbs over resources). A route asks
  for a permission; it never inspects raw roles.
- `Role` is the eight business roles. `ROLE_PERMISSIONS` is the matrix — a role
  grants EXACTLY the listed permissions and nothing else (deny-by-default: an
  unlisted permission is denied).
- `business_role(user)` maps the CURRENTLY-STORED role onto a business role, so
  this layer works on legacy accounts WITHOUT a schema change:
    owner→OWNER, admin→ADMINISTRATOR, user→EMPLOYEE, user_free→READ_ONLY.
  It is also forward-compatible: since the role-model expansion (A1.5,
  `app.models.user.UserRole`), an account may also store one of the eight
  business-role values directly — it is then used as-is, no mapping needed.
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
    # Accounts-receivable cash application — record receipts, allocate/reverse them
    # across issued invoices (Phase 11). A distinct duty from issuing the invoice.
    PAYMENT_READ = "payment.read"
    PAYMENT_WRITE = "payment.write"
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
    # Transport vertical (M3, ADR-P3 rule 5 / ADR-0023) — permissions, not a new
    # role tier. VAT_SUBMIT is split from VAT_WRITE because submitting a claim
    # acquires invoice locks (a consequential, hard-to-reverse action), mirroring
    # how ISSUED_SEND is split from ISSUED_WRITE.
    VAT_READ = "vat.read"
    VAT_WRITE = "vat.write"
    VAT_SUBMIT = "vat.submit"
    TRANSPORT_READ = "transport.read"  # fuel/toll analytics, excise (advisory)


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
    # Transport (M3): full VAT claim read/write/submit + advisory analytics —
    # the VAT refund claim is a money surface this role already owns the peers of.
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
            _P.PAYMENT_READ,
            _P.PAYMENT_WRITE,
            _P.REPORT_READ,
            _P.EXPORT_RUN,
            _P.AUDIT_READ,
            _P.VAT_READ,
            _P.VAT_WRITE,
            _P.VAT_SUBMIT,
            _P.TRANSPORT_READ,
        }
    ),
    # Books the numbers: read/write invoices/expenses/issuing + apply cash + export
    # + report. No approving, no sending, no administration. Transport (M3):
    # books claim data (VAT_WRITE) but does not SUBMIT one — submission acquires
    # invoice locks, a consequential action mirroring the ISSUED_SEND split above.
    Role.ACCOUNTANT: frozenset(
        {
            _P.INVOICE_READ,
            _P.INVOICE_WRITE,
            _P.EXPENSE_READ,
            _P.EXPENSE_WRITE,
            _P.ISSUED_READ,
            _P.ISSUED_WRITE,
            _P.PAYMENT_READ,
            _P.PAYMENT_WRITE,
            _P.REPORT_READ,
            _P.EXPORT_RUN,
            _P.VAT_READ,
            _P.VAT_WRITE,
            _P.TRANSPORT_READ,
        }
    ),
    # Approves expenses AND supplier invoices; otherwise read-only on the money surfaces.
    # Transport is out of this role's remit today (it holds no ISSUED_READ/
    # PAYMENT_READ either — narrow AP/expense-approval focus, not "every domain").
    Role.APPROVER: frozenset(
        {
            _P.EXPENSE_READ,
            _P.EXPENSE_APPROVE,
            _P.INVOICE_READ,
            _P.INVOICE_APPROVE,
            _P.REPORT_READ,
        }
    ),
    # Submits their own expenses; reads invoices. No transport access (same
    # narrow-remit reasoning as APPROVER above).
    Role.EMPLOYEE: frozenset({_P.EXPENSE_READ, _P.EXPENSE_WRITE, _P.INVOICE_READ}),
    # Read-everything for assurance + read the audit trail + run exports.
    # Transport (M3): read-only visibility over VAT claims and analytics — this
    # role's whole purpose is reading every money surface for assurance.
    Role.AUDITOR: frozenset(
        {
            _P.INVOICE_READ,
            _P.EXPENSE_READ,
            _P.ISSUED_READ,
            _P.PAYMENT_READ,
            _P.REPORT_READ,
            _P.AUDIT_READ,
            _P.EXPORT_RUN,
            _P.VAT_READ,
            _P.TRANSPORT_READ,
        }
    ),
    # Pure read of the money surfaces. Transport (M3): a VAT refund claim is a
    # money surface like any other read-only user already sees the peers of.
    Role.READ_ONLY: frozenset(
        {
            _P.INVOICE_READ,
            _P.EXPENSE_READ,
            _P.ISSUED_READ,
            _P.PAYMENT_READ,
            _P.REPORT_READ,
            _P.VAT_READ,
            _P.TRANSPORT_READ,
        }
    ),
}

# The original 4-tier stored role → a business role (backward compatibility;
# still the values `owner`/`admin`/`user`/`user_free` in `UserRole`).
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


# --------------------------------------------------------------------------- #
# Structural authorization (ADR-0024)
#
# Routes declare their permission as a router/route dependency built by
# `app.api.deps.require_perm(...)` (the factory lives in the API layer because a
# FastAPI dependency must resolve `CurrentUser` at definition time and `core`
# must not import `app.api` — see the layering tests). The introspection
# contract, however, is defined HERE so CI and tooling depend only on core:
# every gate dependency carries its declaration under `PERMISSIONS_ATTR`, and
# `declared_permissions()` reads it back without executing anything.
# --------------------------------------------------------------------------- #

# Attribute a gate dependency carries so tests/CI can enumerate what a route
# declares. The value is a non-empty tuple of `Permission` members — or, for the
# cross-tenant operator gate (stricter than ANY tenant permission), the
# `PLATFORM_ADMIN` sentinel.
PERMISSIONS_ATTR = "__ffs_permissions__"

# Sentinel declaration for platform-operator-only routes (`is_platform_admin`).
# Not a `Permission`: an org OWNER holds every permission but must NEVER pass a
# platform gate, so the gate cannot be expressed in the tenant vocabulary.
PLATFORM_ADMIN = "platform_admin"


def declared_permissions(dep: object) -> tuple[object, ...]:
    """The permission declaration a gate dependency carries (empty if none)."""
    value = getattr(dep, PERMISSIONS_ATTR, ())
    return tuple(value) if value else ()


# The explicitly-reviewed allow-list of routes that legitimately carry NO
# permission declaration. Keyed by (HTTP method, full mounted path); the value
# is the REASON the route may stay unclassified — an entry with an empty reason
# fails CI (tests/test_authz_coverage.py), and so does an entry that no longer
# resolves to a live route (no stale/phantom classifications).
#
# Three legitimate categories:
#   1. public bootstrap / token-credentialed endpoints (register, login, reset,
#      invites, SSO) — the token IS the credential;
#   2. webhook receivers that carry their own authentication (payload signature,
#      inbound-address token, SCIM bearer);
#   3. authenticated SELF-SERVICE endpoints that touch only the caller's own
#      account/session/usage — no permission is implied by any role.
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    # --- infrastructure probes (no tenant data) ---
    ("GET", "/health"): "liveness probe for load balancers — no I/O, no data",
    ("GET", "/health/ready"): "readiness probe — reports DB reachability only",
    ("GET", "/health/queue"): "queue SLO probe for uptime checks — aggregate counts only",
    ("GET", "/metrics"): "Prometheus scrape endpoint — infrastructure metrics only",
    # --- auth bootstrap (public; the token/credential is the authentication) ---
    ("POST", "/api/v1/auth/register"): "public bootstrap: account + workspace registration",
    ("POST", "/api/v1/auth/login"): "public bootstrap: credential login",
    (
        "POST",
        "/api/v1/auth/verify-email",
    ): "public: the emailed verification token is the credential",
    (
        "POST",
        "/api/v1/auth/forgot-password",
    ): "public bootstrap: reset request (never enumerates accounts)",
    ("POST", "/api/v1/auth/reset-password"): "public: the emailed reset token is the credential",
    (
        "GET",
        "/api/v1/auth/invite/{token}",
    ): "public: the invitation token is the credential (preview)",
    (
        "POST",
        "/api/v1/auth/accept-invite",
    ): "public bootstrap: the invitation token is the credential",
    ("GET", "/api/v1/auth/sso/{slug}/authorize"): "public SSO: begins the OIDC login redirect",
    (
        "GET",
        "/api/v1/auth/sso/callback",
    ): "public SSO: OIDC redirect back from the IdP (state-signed)",
    (
        "GET",
        "/api/v1/auth/sso/{slug}/saml/metadata",
    ): "public SSO: SP metadata XML for IdP registration",
    ("GET", "/api/v1/auth/sso/{slug}/saml/login"): "public SSO: begins the SAML login redirect",
    (
        "POST",
        "/api/v1/auth/sso/saml/acs",
    ): "public SSO: SAML ACS (deliberate 501 until XML-DSig is vetted)",
    # --- authenticated self-service (the caller's OWN account/session/usage) ---
    ("GET", "/api/v1/auth/me"): "authenticated self-service: the caller's own profile",
    (
        "PUT",
        "/api/v1/auth/bank-details",
    ): "authenticated self-service: the caller's OWN payout IBAN/BIC only",
    (
        "GET",
        "/api/v1/auth/permissions",
    ): "authenticated self-service: own resolved permissions (advisory for the UI)",
    (
        "GET",
        "/api/v1/auth/authz-matrix",
    ): "authenticated: the static role→permission matrix — no tenant data",
    (
        "GET",
        "/api/v1/auth/organizations",
    ): "authenticated self-service: the caller's own memberships (org switcher)",
    (
        "POST",
        "/api/v1/auth/switch-org/{org_id}",
    ): "authenticated self-service: membership-gated in-handler, opaque 404",
    ("POST", "/api/v1/auth/logout"): "authenticated self-service: revoke the caller's own session",
    ("GET", "/api/v1/auth/sessions"): "authenticated self-service: list the caller's own sessions",
    (
        "POST",
        "/api/v1/auth/sessions/revoke-others",
    ): "authenticated self-service: revoke own other sessions",
    (
        "DELETE",
        "/api/v1/auth/sessions/{session_id}",
    ): "authenticated self-service: own session only, opaque 404",
    (
        "POST",
        "/api/v1/auth/resend-verification",
    ): "authenticated self-service: re-send own verification mail",
    (
        "GET",
        "/api/v1/access/usage",
    ): "authenticated self-service: the caller's own usage vs quota (every tier reads its own; asserted by test_access)",
    # --- webhook receivers (authenticated by their own mechanism) ---
    (
        "POST",
        "/api/v1/billing/webhook",
    ): "webhook: the Stripe payload signature is the authentication",
    (
        "GET",
        "/api/v1/billing/everypay/return",
    ): "payment redirect: verified server-side by reference, never trusted",
    (
        "POST",
        "/api/v1/billing/everypay/callback",
    ): "webhook: EveryPay server-to-server, verified by reference lookup",
    (
        "POST",
        "/api/v1/email/inbound",
    ): "webhook: mandatory shared secret authenticates the provider; recipient token routes",
    (
        "POST",
        "/api/v1/email/inbound/mailgun",
    ): "webhook: Mailgun's own HMAC signature (timestamp+token) authenticates; recipient token routes",
    # --- SCIM provisioning (authenticated by the connection's own bearer token) ---
    (
        "POST",
        "/api/v1/scim/v2/Users",
    ): "SCIM: the connection's own bearer token authenticates (require_scim)",
    (
        "GET",
        "/api/v1/scim/v2/Users",
    ): "SCIM: the connection's own bearer token authenticates (require_scim)",
    (
        "GET",
        "/api/v1/scim/v2/Users/{user_id}",
    ): "SCIM: the connection's own bearer token authenticates (require_scim)",
    (
        "PUT",
        "/api/v1/scim/v2/Users/{user_id}",
    ): "SCIM: the connection's own bearer token authenticates (require_scim)",
    (
        "PATCH",
        "/api/v1/scim/v2/Users/{user_id}",
    ): "SCIM: the connection's own bearer token authenticates (require_scim)",
    (
        "DELETE",
        "/api/v1/scim/v2/Users/{user_id}",
    ): "SCIM: the connection's own bearer token authenticates (require_scim)",
}
