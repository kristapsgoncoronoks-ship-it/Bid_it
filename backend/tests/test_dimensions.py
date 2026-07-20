"""Cost-allocation dimensions on invoices + expenses, and the by-dimension analytics."""
import pytest


async def _invoice(auth_client, number: str, total_lines, **dims) -> dict:
    body = {
        "vendor_name": "Shell Fleet",
        "invoice_number": number,
        "issue_date": "2026-05-10",
        "line_items": [{"description": "Diesel", "quantity": "1", "unit_price": str(total_lines), "tax_rate": "0"}],
        **dims,
    }
    r = await auth_client.post("/api/v1/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_invoice_carries_dimensions(auth_client):
    inv = await _invoice(auth_client, "INV-1", "100.00", vehicle="TRUCK-01", project="P-North", cost_center="CC-9")
    assert inv["vehicle"] == "TRUCK-01"
    assert inv["project"] == "P-North"
    assert inv["cost_center"] == "CC-9"
    assert inv["department"] is None

    got = (await auth_client.get(f"/api/v1/invoices/{inv['id']}")).json()
    assert got["vehicle"] == "TRUCK-01"


@pytest.mark.asyncio
async def test_patch_invoice_dimensions(auth_client):
    inv = await _invoice(auth_client, "INV-2", "50.00")
    assert inv["vehicle"] is None

    # Set a dimension.
    r = await auth_client.patch(f"/api/v1/invoices/{inv['id']}", json={"vehicle": "VAN-7"})
    assert r.status_code == 200
    assert r.json()["vehicle"] == "VAN-7"

    # Omitting the field leaves it unchanged; an explicit null clears it.
    r2 = await auth_client.patch(f"/api/v1/invoices/{inv['id']}", json={"project": "P1"})
    assert r2.json()["vehicle"] == "VAN-7" and r2.json()["project"] == "P1"
    r3 = await auth_client.patch(f"/api/v1/invoices/{inv['id']}", json={"vehicle": None})
    assert r3.json()["vehicle"] is None and r3.json()["project"] == "P1"


@pytest.mark.asyncio
async def test_by_dimension_breakdown(auth_client):
    await _invoice(auth_client, "A", "100.00", vehicle="TRUCK-01")
    await _invoice(auth_client, "B", "40.00", vehicle="TRUCK-01")
    await _invoice(auth_client, "C", "25.00", vehicle="VAN-7")
    await _invoice(auth_client, "D", "10.00")  # untagged

    r = await auth_client.get("/api/v1/analytics/by-dimension", params={"dimension": "vehicle"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dimension"] == "vehicle" and body["label"] == "Vehicle"
    rows = {row["value"]: row for row in body["rows"]}
    assert rows["TRUCK-01"]["total"] == "140.00" and rows["TRUCK-01"]["invoice_count"] == 2
    assert rows["VAN-7"]["total"] == "25.00"
    assert rows["(unassigned)"]["total"] == "10.00"
    # Breakdown sums to the tenant's total spend.
    assert body["total"] == "175.00"


@pytest.mark.asyncio
async def test_by_dimension_rejects_unknown(auth_client):
    r = await auth_client.get("/api/v1/analytics/by-dimension", params={"dimension": "colour"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_dimensions_catalog(auth_client):
    r = await auth_client.get("/api/v1/analytics/dimensions")
    assert r.status_code == 200
    keys = {d["key"] for d in r.json()}
    assert keys == {"cost_center", "department", "project", "vehicle", "property_ref"}


@pytest.mark.asyncio
async def test_expense_item_dimensions(auth_client):
    await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    body = {
        "title": "May fuel",
        "items": [{
            "spend_date": "2026-05-05", "category": "transport", "description": "Diesel",
            "amount": "80.00", "vehicle": "TRUCK-01", "project": "P-North",
        }],
    }
    r = await auth_client.post("/api/v1/expenses", json=body)
    assert r.status_code == 201, r.text
    item = r.json()["items"][0]
    assert item["vehicle"] == "TRUCK-01"
    assert item["project"] == "P-North"

    # Patch a dimension on the draft item.
    report_id = r.json()["id"]
    patched = await auth_client.patch(
        f"/api/v1/expenses/{report_id}/items/{item['id']}", json={"cost_center": "CC-1"}
    )
    assert patched.status_code == 200
    got = next(i for i in patched.json()["items"] if i["id"] == item["id"])
    assert got["cost_center"] == "CC-1"
    assert got["vehicle"] == "TRUCK-01"  # unchanged


@pytest.mark.asyncio
async def test_dimensions_tenant_isolated(auth_client, client):
    await _invoice(auth_client, "SECRET-1", "500.00", vehicle="TRUCK-SECRET")

    reg = await client.post("/api/v1/auth/register", json={
        "organization_name": "Other Co", "name": "O", "email": "o2@o.io", "password": "supersecret2",
    })
    client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"
    body = (await client.get("/api/v1/analytics/by-dimension", params={"dimension": "vehicle"})).json()
    # The other tenant sees none of the first tenant's tagged spend.
    assert body["rows"] == []
    assert body["total"] == "0"
