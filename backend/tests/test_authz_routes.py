"""WO-1 Step 3 — the confirmed-open endpoints are now permission-gated.

One denied-role and one granted-role case per newly-gated surface. The denied
role is the LOWEST role the matrix refuses; the granted role is the LOWEST role
the matrix allows (never just the owner). Note: `user_free` resolves to
READ_ONLY, which legitimately HOLDS `report.read` in the (unchanged) matrix —
so for the analytics KPIs the denied role is EMPLOYEE (`user`), which has no
`report.read`, and READ_ONLY is a granted case.
"""

from __future__ import annotations

import pytest

# The six analytics KPI endpoints that previously carried no permission check.
KPI_ENDPOINTS = (
    "/api/v1/analytics/summary",
    "/api/v1/analytics/spend-over-time",
    "/api/v1/analytics/top-vendors",
    "/api/v1/analytics/by-category",
    "/api/v1/analytics/by-status",
    "/api/v1/analytics/dimensions",
)


@pytest.mark.asyncio
async def test_analytics_kpis_require_report_read(role_client):
    employee = await role_client("user")  # EMPLOYEE — no report.read
    ro = await role_client("user_free")  # READ_ONLY — holds report.read
    for path in KPI_ENDPOINTS:
        denied = await employee.get(path)
        assert denied.status_code == 403, f"{path}: expected 403 for EMPLOYEE"
        assert "detail" in denied.json()
        granted = await ro.get(path)
        assert granted.status_code == 200, f"{path}: expected 200 for READ_ONLY"


@pytest.mark.asyncio
async def test_team_members_requires_member_read(role_client):
    ro = await role_client("user_free")
    assert (await ro.get("/api/v1/team/members")).status_code == 403
    admin = await role_client("admin")  # lowest role with member.read
    assert (await admin.get("/api/v1/team/members")).status_code == 200


@pytest.mark.asyncio
async def test_webhooks_list_requires_settings_manage(role_client):
    ro = await role_client("user_free")
    assert (await ro.get("/api/v1/webhooks")).status_code == 403
    admin = await role_client("admin")
    assert (await admin.get("/api/v1/webhooks")).status_code == 200


@pytest.mark.asyncio
async def test_jobs_list_requires_settings_manage(role_client):
    ro = await role_client("user_free")
    assert (await ro.get("/api/v1/jobs")).status_code == 403
    admin = await role_client("admin")
    assert (await admin.get("/api/v1/jobs")).status_code == 200


@pytest.mark.asyncio
async def test_access_matrix_requires_settings_manage(role_client):
    ro = await role_client("user_free")
    assert (await ro.get("/api/v1/access/matrix")).status_code == 403
    admin = await role_client("admin")
    assert (await admin.get("/api/v1/access/matrix")).status_code == 200


@pytest.mark.asyncio
async def test_access_usage_stays_self_service(role_client):
    """Documented exception to 'gate /access/*': the caller's OWN usage/quota is
    self-service for every tier (asserted for the free tier by test_access too)."""
    ro = await role_client("user_free")
    assert (await ro.get("/api/v1/access/usage")).status_code == 200


@pytest.mark.asyncio
async def test_modules_list_requires_settings_manage(role_client):
    ro = await role_client("user_free")
    assert (await ro.get("/api/v1/modules")).status_code == 403
    admin = await role_client("admin")
    assert (await admin.get("/api/v1/modules")).status_code == 200


@pytest.mark.asyncio
async def test_settings_validation_requires_settings_manage(role_client):
    ro = await role_client("user_free")
    assert (await ro.get("/api/v1/settings/validation")).status_code == 403
    admin = await role_client("admin")
    assert (await admin.get("/api/v1/settings/validation")).status_code == 200


@pytest.mark.asyncio
async def test_vendor_mutations_require_invoice_write(role_client):
    """The WO-1 headline gap: POST/PATCH /vendors set the IBAN that payment runs
    pay out to and previously carried NO permission check."""
    ro = await role_client("user_free")
    assert (
        await ro.post("/api/v1/vendors", json={"name": "Rogue Vendor", "iban": "LT00 0000"})
    ).status_code == 403

    acct = await role_client("user")  # EMPLOYEE — invoice.read only, no write
    assert (await acct.post("/api/v1/vendors", json={"name": "Rogue Vendor 2"})).status_code == 403

    admin = await role_client("admin")
    created = await admin.post("/api/v1/vendors", json={"name": "Vendor OU"})
    assert created.status_code == 201
    vid = created.json()["id"]
    # PATCH follows the same declaration.
    assert (
        await admin.patch(f"/api/v1/vendors/{vid}", json={"category": "fuel"})
    ).status_code == 200
    denied_patch = await ro.patch(f"/api/v1/vendors/{vid}", json={"iban": "LT99 9999"})
    assert denied_patch.status_code == 403


@pytest.mark.asyncio
async def test_billing_read_requires_billing_manage(role_client):
    """Billing state (plan, subscription ids) is owner-only under the matrix —
    the read was previously open to any member (tightened by ADR-0024)."""
    admin = await role_client("admin")  # ADMINISTRATOR lacks billing.manage
    assert (await admin.get("/api/v1/billing")).status_code == 403
    owner = await role_client("owner")
    assert (await owner.get("/api/v1/billing")).status_code == 200
