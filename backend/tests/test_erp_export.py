"""Accounting/ERP exports (Phase 3.10): generic + Xero + QuickBooks CSV of the
received-invoice ledger, formula-injection-safe, read-only."""

import csv
import io

import pytest
from sqlalchemy import select, update

from app.models.invoice import Invoice, WorkflowState
from app.models.issuer import IssuerProfile
from app.models.membership import Membership


async def _invoice(auth_client, number, vendor, total_net, **extra):
    body = {
        "vendor_name": vendor,
        "invoice_number": number,
        "issue_date": "2026-05-10",
        "line_items": [
            {"description": "Item", "quantity": "1", "unit_price": str(total_net), "tax_rate": "0"}
        ],
        **extra,
    }
    r = await auth_client.post("/api/v1/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _export(auth_client, db_session, start="2026-05-01", end="2026-05-31"):
    entity = (await auth_client.get("/api/v1/issuer/registry")).json()[0]
    org_id = await db_session.scalar(
        select(IssuerProfile.org_id).where(IssuerProfile.id == entity["id"])
    )
    # These fixtures represent reviewed AP records; capture alone remains draft.
    await db_session.execute(
        update(Invoice)
        .where(Invoice.org_id == org_id)
        .values(legal_entity_id=entity["id"], workflow_state=WorkflowState.approved)
    )
    await db_session.commit()
    response = await auth_client.post(
        "/api/v1/export/accounting/preview",
        json={"entity_id": entity["id"], "date_from": start, "date_to": end, "currency": "EUR"},
    )
    assert response.status_code == 200, response.text
    if response.json()["token"] is None:
        return response
    return await auth_client.post(
        "/api/v1/export/accounting/download", json={"token": response.json()["token"]}
    )


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


@pytest.mark.asyncio
async def test_generic_export_has_net_vat_gross_and_dimensions(auth_client, db_session):
    await _invoice(auth_client, "INV-1", "Shell", "100.00", vehicle="TRUCK-01", cost_center="CC-9")
    r = await _export(auth_client, db_session)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = _rows(r.text)
    assert rows[0][:6] == ["Date", "Due Date", "Supplier", "Invoice Number", "Currency", "Net"]
    data = rows[1]
    assert data[2] == "Shell" and data[3] == "INV-1"
    assert data[5] == "100.00"  # Net
    assert "TRUCK-01" in data and "CC-9" in data


@pytest.mark.asyncio
async def test_xero_export_requires_reviewed_template_and_mappings(auth_client):
    r = await auth_client.get("/api/v1/export/accounting?fmt=xero")
    assert r.status_code == 409
    assert r.json()["code"] == "export_adapter_unconfigured"


@pytest.mark.asyncio
async def test_quickbooks_export_requires_verified_product_region_contract(auth_client):
    r = await auth_client.get("/api/v1/export/accounting?fmt=quickbooks")
    assert r.status_code == 409
    assert r.json()["code"] == "export_adapter_unconfigured"


@pytest.mark.asyncio
async def test_export_is_formula_injection_safe(auth_client, db_session):
    # A supplier name starting with '=' must be neutralised in the CSV.
    await _invoice(auth_client, "INV-INJ", "=cmd|'/c calc'!A1", "10.00")
    r = await _export(auth_client, db_session)
    rows = _rows(r.text)
    supplier_cell = rows[1][2]
    assert supplier_cell.startswith("'=")  # prefixed with a quote → not a formula


@pytest.mark.asyncio
async def test_unknown_format_and_empty_period(auth_client):
    bad = await auth_client.get("/api/v1/export/accounting?fmt=sap")
    assert bad.status_code == 400
    empty = await auth_client.get(
        "/api/v1/export/accounting?fmt=generic&from=2000-01-01&to=2000-12-31"
    )
    assert empty.status_code == 409
    assert empty.json()["code"] == "export_preview_required"


@pytest.mark.asyncio
async def test_export_requires_admin(auth_client, db_session):
    from sqlalchemy import select, update

    from app.models.organization import Organization
    from app.models.user import UserRole

    await _invoice(auth_client, "INV-A", "Acme", "5.00")
    org = await db_session.scalar(select(Organization.id))
    await db_session.execute(
        update(Membership).where(Membership.org_id == org).values(role=UserRole.user)
    )
    await db_session.commit()
    r = await auth_client.get("/api/v1/export/accounting?fmt=generic")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_export_is_tenant_scoped(auth_client, client):
    await _invoice(auth_client, "SECRET", "SecretVendor", "999.00")

    entity = (await auth_client.get("/api/v1/issuer/registry")).json()[0]["id"]
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Co",
            "name": "O",
            "email": "oe@o.io",
            "password": "supersecret2",
        },
    )
    client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"
    r = await client.post(
        "/api/v1/export/accounting/preview",
        json={
            "entity_id": entity,
            "date_from": "2026-05-01",
            "date_to": "2026-05-31",
            "currency": "EUR",
        },
    )
    # The other tenant has no invoices → nothing to export (never sees SECRET).
    assert r.status_code == 404
