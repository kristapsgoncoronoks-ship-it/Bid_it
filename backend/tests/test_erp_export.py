"""Accounting/ERP exports (Phase 3.10): generic + Xero + QuickBooks CSV of the
received-invoice ledger, formula-injection-safe, read-only."""
import csv
import io

import pytest


async def _invoice(auth_client, number, vendor, total_net, **extra):
    body = {
        "vendor_name": vendor,
        "invoice_number": number,
        "issue_date": "2026-05-10",
        "line_items": [{"description": "Item", "quantity": "1", "unit_price": str(total_net), "tax_rate": "0"}],
        **extra,
    }
    r = await auth_client.post("/api/v1/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


@pytest.mark.asyncio
async def test_generic_export_has_net_vat_gross_and_dimensions(auth_client):
    await _invoice(auth_client, "INV-1", "Shell", "100.00", vehicle="TRUCK-01", cost_center="CC-9")
    r = await auth_client.get("/api/v1/export/accounting?fmt=generic")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = _rows(r.text)
    assert rows[0][:6] == ["Date", "Due Date", "Supplier", "Invoice Number", "Currency", "Net"]
    data = rows[1]
    assert data[2] == "Shell" and data[3] == "INV-1"
    assert data[5] == "100.00"                 # Net
    assert "TRUCK-01" in data and "CC-9" in data


@pytest.mark.asyncio
async def test_xero_export_uses_bill_template_columns(auth_client):
    await _invoice(auth_client, "INV-X", "Globex", "250.00")
    r = await auth_client.get("/api/v1/export/accounting?fmt=xero")
    assert r.status_code == 200
    rows = _rows(r.text)
    assert rows[0][0] == "*ContactName" and "*UnitAmount" in rows[0] and "*TaxType" in rows[0]
    row = rows[1]
    assert row[0] == "Globex" and row[1] == "INV-X"
    assert row[5] == "1"          # Quantity
    assert row[6] == "250.00"     # UnitAmount = net


@pytest.mark.asyncio
async def test_quickbooks_export_uses_gross_amount(auth_client):
    await _invoice(auth_client, "INV-Q", "Initech", "80.00")
    r = await auth_client.get("/api/v1/export/accounting?fmt=quickbooks")
    rows = _rows(r.text)
    assert rows[0][0] == "Supplier" and "Bill No" in rows[0]
    assert rows[1][0] == "Initech" and rows[1][1] == "INV-Q"
    assert rows[1][5] == "80.00"   # Amount (gross; 0% tax here)


@pytest.mark.asyncio
async def test_export_is_formula_injection_safe(auth_client):
    # A supplier name starting with '=' must be neutralised in the CSV.
    await _invoice(auth_client, "INV-INJ", "=cmd|'/c calc'!A1", "10.00")
    r = await auth_client.get("/api/v1/export/accounting?fmt=generic")
    rows = _rows(r.text)
    supplier_cell = rows[1][2]
    assert supplier_cell.startswith("'=")   # prefixed with a quote → not a formula


@pytest.mark.asyncio
async def test_unknown_format_and_empty_period(auth_client):
    bad = await auth_client.get("/api/v1/export/accounting?fmt=sap")
    assert bad.status_code == 400
    empty = await auth_client.get("/api/v1/export/accounting?fmt=generic&from=2000-01-01&to=2000-12-31")
    assert empty.status_code == 404


@pytest.mark.asyncio
async def test_export_requires_admin(auth_client, db_session):
    from sqlalchemy import select, update

    from app.models.organization import Organization
    from app.models.user import User, UserRole

    await _invoice(auth_client, "INV-A", "Acme", "5.00")
    org = await db_session.scalar(select(Organization.id))
    await db_session.execute(update(User).where(User.org_id == org).values(role=UserRole.user))
    await db_session.commit()
    r = await auth_client.get("/api/v1/export/accounting?fmt=generic")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_export_is_tenant_scoped(auth_client, client):
    await _invoice(auth_client, "SECRET", "SecretVendor", "999.00")

    reg = await client.post("/api/v1/auth/register", json={
        "organization_name": "Other Co", "name": "O", "email": "oe@o.io", "password": "supersecret2",
    })
    client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"
    r = await client.get("/api/v1/export/accounting?fmt=generic")
    # The other tenant has no invoices → nothing to export (never sees SECRET).
    assert r.status_code == 404
