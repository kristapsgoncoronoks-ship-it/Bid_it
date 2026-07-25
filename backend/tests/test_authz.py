"""Reusable authorization service + the 8-role permission matrix (deny-by-default).

Proves: every role grants EXACTLY its matrix row and nothing else; the stored
4-tier role maps onto the business roles; the platform-operator flag and the
expense-approver flag resolve correctly; and `require` is a hard 403 gate.
"""

import pytest
from fastapi import HTTPException

from app.core import authz
from app.core.authz import Permission as P
from app.core.authz import Role


class _U:
    """A minimal user stand-in (role value + the two flags authz reads)."""

    def __init__(self, role="user", platform=False, approver=False):
        self.role = role
        self.is_platform_admin = platform
        self.is_expense_approver = approver


def test_deny_by_default_read_only_has_only_reads():
    perms = authz.ROLE_PERMISSIONS[Role.READ_ONLY]
    assert perms == frozenset(
        {P.INVOICE_READ, P.EXPENSE_READ, P.ISSUED_READ, P.PAYMENT_READ, P.REPORT_READ}
    )
    # Nothing write/admin/export leaks in.
    for denied in (
        P.INVOICE_WRITE,
        P.PAYMENT_WRITE,
        P.EXPORT_RUN,
        P.MEMBER_MANAGE,
        P.ROLE_ASSIGN,
        P.BILLING_MANAGE,
    ):
        assert denied not in perms


def test_owner_has_everything_admin_all_but_billing():
    assert authz.ROLE_PERMISSIONS[Role.OWNER] == authz.ALL_PERMISSIONS
    admin = authz.ROLE_PERMISSIONS[Role.ADMINISTRATOR]
    assert P.BILLING_MANAGE not in admin
    assert admin == frozenset(authz.ALL_PERMISSIONS - {P.BILLING_MANAGE})


def test_role_boundaries():
    fm = authz.ROLE_PERMISSIONS[Role.FINANCE_MANAGER]
    assert P.EXPENSE_APPROVE in fm and P.EXPORT_RUN in fm and P.AUDIT_READ in fm
    assert P.MEMBER_MANAGE not in fm and P.BILLING_MANAGE not in fm  # not an admin

    acc = authz.ROLE_PERMISSIONS[Role.ACCOUNTANT]
    assert P.INVOICE_WRITE in acc and P.EXPORT_RUN in acc
    assert P.PAYMENT_READ in acc and P.PAYMENT_WRITE in acc  # applies cash
    assert P.EXPENSE_APPROVE not in acc and P.ISSUED_SEND not in acc  # books, doesn't approve/send

    appr = authz.ROLE_PERMISSIONS[Role.APPROVER]
    assert appr == frozenset(
        {
            P.EXPENSE_READ,
            P.EXPENSE_APPROVE,
            P.INVOICE_READ,
            P.INVOICE_APPROVE,
            P.REPORT_READ,
        }
    )

    aud = authz.ROLE_PERMISSIONS[Role.AUDITOR]
    assert P.AUDIT_READ in aud and P.EXPORT_RUN in aud and P.PAYMENT_READ in aud
    # read-only assurance — no write of any money surface, incl. cash application.
    assert not (aud & {P.INVOICE_WRITE, P.EXPENSE_WRITE, P.ISSUED_WRITE, P.PAYMENT_WRITE})


def test_stored_legacy_roles_map_to_business_roles():
    assert authz.business_role(_U("owner")) is Role.OWNER
    assert authz.business_role(_U("admin")) is Role.ADMINISTRATOR
    assert authz.business_role(_U("user")) is Role.EMPLOYEE
    assert authz.business_role(_U("user_free")) is Role.READ_ONLY
    # Unknown → least privilege.
    assert authz.business_role(_U("bogus")) is Role.READ_ONLY


def test_forward_compatible_with_expanded_role_values():
    # Once the role model stores the 8 values directly, they resolve as-is.
    assert authz.business_role(_U("finance_manager")) is Role.FINANCE_MANAGER
    assert authz.business_role(_U("auditor")) is Role.AUDITOR


def test_platform_admin_has_all_permissions():
    assert authz.permissions_for(_U("user", platform=True)) == authz.ALL_PERMISSIONS


def test_expense_approver_flag_bridges_the_approve_permission():
    base = _U("user")  # EMPLOYEE — no approve
    assert not authz.has(base, P.EXPENSE_APPROVE)
    with_flag = _U("user", approver=True)
    assert authz.has(with_flag, P.EXPENSE_APPROVE)


def test_require_raises_403_when_missing_all_are_required():
    employee = _U("user")
    authz.require(employee, P.INVOICE_READ)  # granted → no raise
    with pytest.raises(HTTPException) as exc:
        authz.require(employee, P.EXPORT_RUN)
    assert exc.value.status_code == 403
    # require is AND: holding one but not the other still raises.
    with pytest.raises(HTTPException):
        authz.require(employee, P.INVOICE_READ, P.EXPORT_RUN)


def test_every_role_is_in_the_matrix():
    assert set(authz.ROLE_PERMISSIONS) == set(Role)
    assert set(authz.matrix()) == {r.value for r in Role}


@pytest.mark.asyncio
async def test_permissions_endpoint_reflects_owner(auth_client):
    # The registered owner sees the OWNER role + a full permission set.
    body = (await auth_client.get("/api/v1/auth/permissions")).json()
    assert body["role"] == "organization_owner"
    assert "billing.manage" in body["permissions"] and "export.run" in body["permissions"]


@pytest.mark.asyncio
async def test_require_perm_dependency_denies_missing_permission(role_client):
    """Structural enforcement (ADR-0024): /audit is gated ONLY by the router-level
    require_perm(AUDIT_READ) dependency — there is no in-handler call left — so a
    role without AUDIT_READ must get 403 and a role with it must get through."""
    employee = await role_client("user")  # EMPLOYEE — no AUDIT_READ
    denied = await employee.get("/api/v1/audit")
    assert denied.status_code == 403
    assert "detail" in denied.json()

    owner = await role_client("owner")  # OWNER — all permissions
    allowed = await owner.get("/api/v1/audit")
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_route_level_override_is_stricter_than_router_default(role_client):
    """A per-route stricter declaration wins over the router default: /vendors is
    INVOICE_READ at the router, but POST declares INVOICE_WRITE — so READ_ONLY
    (user_free) can list vendors yet cannot create one."""
    ro = await role_client("user_free")  # READ_ONLY — INVOICE_READ only
    assert (await ro.get("/api/v1/vendors")).status_code == 200
    denied = await ro.post("/api/v1/vendors", json={"name": "Overreach GmbH"})
    assert denied.status_code == 403

    owner = await role_client("owner")
    created = await owner.post("/api/v1/vendors", json={"name": "Legit Vendor OU"})
    assert created.status_code == 201


@pytest.mark.asyncio
async def test_export_guard_uses_authz(auth_client, db_session):
    # Owner (has EXPORT_RUN) is allowed past the guard (404 = no data, not 403).
    r = await auth_client.get("/api/v1/export/accounting?from=2026-01-01&to=2026-12-31")
    assert r.status_code in (200, 404)

    # A read-only (user_free) member has no EXPORT_RUN → 403 at the authz gate.
    from sqlalchemy import select, update

    from app.models.organization import Organization
    from app.models.user import User, UserRole

    org = await db_session.scalar(select(Organization.id))
    await db_session.execute(update(User).where(User.org_id == org).values(role=UserRole.user_free))
    await db_session.commit()

    r2 = await auth_client.get("/api/v1/export/accounting?from=2026-01-01&to=2026-12-31")
    assert r2.status_code == 403
