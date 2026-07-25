"""Vendor write-path controls (WO-2): permission gates, IBAN format refusal,
audit on create, optimistic concurrency, opaque cross-tenant 404."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.vendor import Vendor


@pytest.mark.asyncio
async def test_invalid_iban_rejected(auth_client, db_session):
    """A structurally invalid IBAN is refused at write with the stable code —
    and no vendor row is created."""
    r = await auth_client.post(
        "/api/v1/vendors", json={"name": "Bad Bank Co", "iban": "DE00000000000000000000"}
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_iban"
    row = await db_session.scalar(select(Vendor).where(Vendor.name == "Bad Bank Co"))
    assert row is None


@pytest.mark.asyncio
async def test_invalid_bic_rejected(auth_client):
    r = await auth_client.post(
        "/api/v1/vendors",
        json={"name": "Bad BIC Co", "iban": "DE89370400440532013000", "bic": "NOPE"},
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_bic"


@pytest.mark.asyncio
async def test_employee_role_cannot_create_vendor(role_client):
    emp = await role_client("user")  # EMPLOYEE — invoice.read only
    r = await emp.post("/api/v1/vendors", json={"name": "Nope Ltd"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_employee_role_cannot_update_vendor(role_client):
    admin = await role_client("admin")
    created = await admin.post("/api/v1/vendors", json={"name": "Patchable Ltd"})
    assert created.status_code == 201
    vid = created.json()["id"]
    # Same-org employee: holds INVOICE_READ (passes the router gate) but not
    # INVOICE_WRITE — the per-route declaration must refuse the mutation.
    emp = await role_client("user")
    r = await emp.patch(f"/api/v1/vendors/{vid}", json={"category": "fuel"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_read_only_role_can_list_vendors(role_client):
    ro = await role_client("user_free")  # READ_ONLY holds invoice.read
    r = await ro.get("/api/v1/vendors")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_vendor_create_is_audited(auth_client, db_session):
    r = await auth_client.post("/api/v1/vendors", json={"name": "Audit Trail BV"})
    assert r.status_code == 201
    vid = r.json()["id"]
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "vendor.create", AuditEvent.target_id == vid)
    )
    assert event is not None
    assert event.actor_email == "owner@acme.io"  # the caller, not a system actor


@pytest.mark.asyncio
async def test_stale_version_returns_409(auth_client):
    created = await auth_client.post("/api/v1/vendors", json={"name": "Versioned Ltd"})
    assert created.status_code == 201
    vid = created.json()["id"]
    assert created.json()["version"] == 1
    # First write with the read version succeeds and bumps.
    ok = await auth_client.patch(f"/api/v1/vendors/{vid}", json={"category": "fuel", "version": 1})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    # Replaying the OLD version is a 409 with the stable code.
    stale = await auth_client.patch(
        f"/api/v1/vendors/{vid}", json={"category": "tolls", "version": 1}
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_version"


@pytest.mark.asyncio
async def test_cross_tenant_vendor_patch_returns_404_not_403(auth_client, role_client):
    """Invariant §4.4: another tenant's vendor id must be an opaque 404 — the
    caller HOLDS the write permission, so a 403 would leak that the id exists."""
    created = await auth_client.post("/api/v1/vendors", json={"name": "Org A Vendor"})
    assert created.status_code == 201
    vid = created.json()["id"]
    other_admin = await role_client("admin")  # a DIFFERENT org, write-permitted
    r = await other_admin.patch(f"/api/v1/vendors/{vid}", json={"category": "fuel"})
    assert r.status_code == 404, r.text
    # And the list bound to the other org returns zero of org A's rows.
    listing = await other_admin.get("/api/v1/vendors")
    assert listing.status_code == 200
    assert all(v["id"] != vid for v in listing.json())


@pytest.mark.asyncio
async def test_employee_bank_details_reject_invalid_iban(auth_client):
    """The employee payout path gets the same format gate (WO-2 in-scope:
    format validation only, no second-approver flow)."""
    r = await auth_client.put("/api/v1/auth/bank-details", json={"iban": "DE00000000000000000000"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_iban"


@pytest.mark.asyncio
async def test_issuer_profile_rejects_invalid_iban(auth_client):
    """The issuer (debtor) IBAN is format-gated at write time too."""
    r = await auth_client.put(
        "/api/v1/issuer", json={"legal_name": "Us BV", "iban": "NL00ABNA0417164300"}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_iban"
