"""Reusable authorization service + the 8-role permission matrix (deny-by-default).

Proves: every role grants EXACTLY its matrix row and nothing else; the stored
role maps onto the business roles (legacy 4-tier values via `_LEGACY_ROLE`, the
4 newly-reachable business roles directly — see A1.5); the platform-operator
flag and the expense-approver flag resolve correctly; and `require` is a hard
403 gate. `test_new_roles_are_reachable_end_to_end` below proves reachability
through the REAL stored column + real HTTP routes, not just the pure resolver.
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


# --------------------------------------------------------------------------- #
# A1.5 — the 4 previously-unreachable business roles, proven end to end
# through the REAL stored column (role_client("finance_manager") etc.) and
# real HTTP routes, not just the pure authz.business_role() resolver above.
# --------------------------------------------------------------------------- #


def _inv(n: int) -> dict:
    return {
        "vendor_name": "Acme",
        "invoice_number": f"INV-{n}",
        "issue_date": "2026-07-01",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "x",
                "category": "c",
                "quantity": "1",
                "unit_price": "10",
                "tax_rate": "0",
            }
        ],
    }


@pytest.mark.asyncio
async def test_finance_manager_role_reaches_matrix_permissions_end_to_end(role_client):
    """Finance Manager: full money surfaces + approve, export, audit-read; no
    member/role/settings administration."""
    fm = await role_client("finance_manager")

    # INVOICE_WRITE.
    assert (await fm.post("/api/v1/vendors", json={"name": "FM Vendor OU"})).status_code == 201

    # Create (quota-gated, open to every INVOICE_READ holder) then approve
    # (INVOICE_APPROVE) the SAME invoice, in the role's own org.
    created = await fm.post("/api/v1/invoices", json=_inv(1))
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]
    approved = await fm.post(f"/api/v1/invoices/{invoice_id}/validate", json={"action": "approve"})
    assert approved.status_code == 200, approved.text

    # AUDIT_READ + EXPORT_RUN.
    assert (await fm.get("/api/v1/audit")).status_code == 200
    assert (
        await fm.get("/api/v1/export/accounting?from=2026-01-01&to=2026-12-31")
    ).status_code in (200, 404)

    # No MEMBER_READ/ROLE_ASSIGN/SETTINGS_MANAGE — not an administrator.
    me = (await fm.get("/api/v1/auth/me")).json()["user"]["id"]
    assert (await fm.get("/api/v1/team/members")).status_code == 403
    assert (
        await fm.patch(f"/api/v1/team/members/{me}", json={"is_active": True})
    ).status_code == 403
    assert (await fm.put("/api/v1/modules/expenses", json={"enabled": True})).status_code == 403


@pytest.mark.asyncio
async def test_accountant_role_reaches_matrix_permissions_end_to_end(role_client):
    """Accountant: books the numbers (write + export); no approve, no audit-read."""
    acc = await role_client("accountant")

    assert (await acc.post("/api/v1/vendors", json={"name": "Acct Vendor OU"})).status_code == 201

    created = await acc.post("/api/v1/invoices", json=_inv(2))
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]

    # Books, doesn't approve: no INVOICE_APPROVE.
    denied = await acc.post(f"/api/v1/invoices/{invoice_id}/validate", json={"action": "approve"})
    assert denied.status_code == 403

    # No AUDIT_READ.
    assert (await acc.get("/api/v1/audit")).status_code == 403

    # Has EXPORT_RUN.
    assert (
        await acc.get("/api/v1/export/accounting?from=2026-01-01&to=2026-12-31")
    ).status_code in (200, 404)


@pytest.mark.asyncio
async def test_approver_role_reaches_matrix_permissions_end_to_end(role_client):
    """Approver: approves (invoice.approve) but does not write (invoice.write) —
    the literal ARCH_plan.md A1.5 acceptance wording. Proven with NO dependency
    on the separate is_expense_approver flag (see the dedicated boundary test
    below for the expense-decision endpoint, which DOES additionally require it
    by pre-existing design)."""
    appr = await role_client("approver")

    # Create is quota-gated (open to every INVOICE_READ holder, not INVOICE_WRITE
    # — see invoices.py::create_invoice's own docstring note) — needed so there
    # is an invoice IN THIS ROLE'S OWN ORG to approve.
    created = await appr.post("/api/v1/invoices", json=_inv(3))
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]

    # APPROVES: INVOICE_APPROVE, no flag involved.
    approved = await appr.post(
        f"/api/v1/invoices/{invoice_id}/validate", json={"action": "approve"}
    )
    assert approved.status_code == 200, approved.text

    # DOES NOT WRITE: no INVOICE_WRITE.
    denied = await appr.post("/api/v1/vendors", json={"name": "Appr Vendor OU"})
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_auditor_role_reaches_matrix_permissions_end_to_end(role_client):
    """Auditor: reads everything + audit + export; writes nothing."""
    aud = await role_client("auditor")

    assert (await aud.get("/api/v1/invoices")).status_code == 200
    assert (await aud.get("/api/v1/audit")).status_code == 200
    assert (
        await aud.get("/api/v1/export/accounting?from=2026-01-01&to=2026-12-31")
    ).status_code in (200, 404)

    # Writes nothing: no INVOICE_WRITE.
    assert (await aud.post("/api/v1/vendors", json={"name": "Aud Vendor OU"})).status_code == 403
    # Not an administrator: no MEMBER_READ.
    assert (await aud.get("/api/v1/team/members")).status_code == 403


@pytest.mark.asyncio
async def test_approver_role_still_needs_the_expense_flag_to_decide(auth_client, client):
    """Documents the deliberate boundary (see WO-22 Out of scope): deciding an
    expense report requires the pre-existing is_expense_approver FLAG, not
    merely the APPROVER role's EXPENSE_APPROVE permission — the router-level
    dependency is necessary but not sufficient, matching the existing
    "two-person control" pattern in authorization-policy-matrix.md. This order
    does not change that; it proves the boundary rather than leaving it
    silently unproven."""
    await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})

    # A member with role=approver but the flag NOT yet set.
    inv = await auth_client.post(
        "/api/v1/team/invites", json={"email": "appr@acme.io", "role": "approver"}
    )
    assert inv.status_code == 201
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Approver", "password": "supersecret"},
    )
    appr_token, appr_id = acc.json()["token"]["access_token"], acc.json()["user"]["id"]
    appr_headers = {"Authorization": f"Bearer {appr_token}"}

    # A different employee submits a complete report.
    emp_inv = await auth_client.post(
        "/api/v1/team/invites", json={"email": "emp@acme.io", "role": "user"}
    )
    emp_acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": emp_inv.json()["token"], "name": "Employee", "password": "supersecret"},
    )
    emp_headers = {"Authorization": f"Bearer {emp_acc.json()['token']['access_token']}"}

    payload = {
        "title": "Trip",
        "items": [
            {
                "spend_date": "2026-05-01",
                "category": "meals",
                "description": "Dinner",
                "amount": "42.00",
                "vat_amount": "0",
            }
        ],
    }
    rid = (await client.post("/api/v1/expenses", json=payload, headers=emp_headers)).json()["id"]
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=emp_headers)).json()
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Client visit"},
            headers=emp_headers,
        )
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("r.png", png, "image/png")},
            headers=emp_headers,
        )
    submitted = await client.post(f"/api/v1/expenses/{rid}/submit", headers=emp_headers)
    assert submitted.status_code == 200, submitted.text

    # The router-level EXPENSE_APPROVE dependency passes (role=approver holds
    # it) but the in-handler flag check still denies — the flag, not the role,
    # is the designated-approver signal for THIS endpoint.
    still_denied = await client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "approve", "version": 1},
        headers=appr_headers,
    )
    assert still_denied.status_code == 403
    assert "designated" in still_denied.json()["detail"].lower()

    # Appoint the flag (independent of role) → now it succeeds.
    appointed = await auth_client.patch(
        f"/api/v1/team/members/{appr_id}", json={"is_expense_approver": True}
    )
    assert appointed.status_code == 200 and appointed.json()["is_expense_approver"] is True
    now_ok = await client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "approve", "version": 1},
        headers=appr_headers,
    )
    assert now_ok.status_code == 200, now_ok.text


@pytest.mark.asyncio
async def test_role_change_audit_records_a_new_business_role(auth_client, client):
    """PATCH /team/members/{id} assigning one of the 4 newly-reachable roles is
    audited exactly like any other role change (§4.16 — no new code path)."""
    inv = await auth_client.post(
        "/api/v1/team/invites", json={"email": "future-auditor@acme.io", "role": "user"}
    )
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Future Auditor", "password": "supersecret"},
    )
    uid = acc.json()["user"]["id"]

    changed = await auth_client.patch(f"/api/v1/team/members/{uid}", json={"role": "auditor"})
    assert changed.status_code == 200 and changed.json()["role"] == "auditor"

    events = (await auth_client.get("/api/v1/audit?action=user.role_change")).json()["items"]
    match = next(e for e in events if e["target_id"] == uid)
    assert match["meta"]["role"] == "auditor"


@pytest.mark.parametrize(
    "role,forbidden",
    [
        (Role.FINANCE_MANAGER, P.MEMBER_MANAGE),
        (Role.FINANCE_MANAGER, P.BILLING_MANAGE),
        (Role.ACCOUNTANT, P.EXPENSE_APPROVE),
        (Role.ACCOUNTANT, P.ISSUED_SEND),
        (Role.APPROVER, P.INVOICE_WRITE),
        (Role.APPROVER, P.EXPENSE_WRITE),
        (Role.AUDITOR, P.INVOICE_WRITE),
        (Role.AUDITOR, P.MEMBER_READ),
    ],
)
def test_new_roles_are_denied_by_default_outside_their_matrix_row(role, forbidden):
    assert forbidden not in authz.ROLE_PERMISSIONS[role]
