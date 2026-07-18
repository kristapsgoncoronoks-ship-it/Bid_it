"""Supplier benchmarking — independent scorecards and combined price benchmark."""
import pytest


async def _invoice(auth_client, vendor, number, category, qty, unit, status="paid"):
    return await auth_client.post("/api/v1/invoices", json={
        "vendor_name": vendor,
        "invoice_number": number,
        "issue_date": "2026-01-10",
        "status": status,
        "line_items": [
            {"description": f"{category} item", "category": category,
             "quantity": qty, "unit_price": unit, "tax_rate": "0"},
        ],
    })


async def _scenario(auth_client):
    # fuel: Alpha @2.00/u x10, Beta @1.50/u x10  → combined avg 1.75, cheapest Beta
    await _invoice(auth_client, "Alpha", "A-1", "fuel", "10", "2.00")
    await _invoice(auth_client, "Beta", "B-1", "fuel", "10", "1.50")
    # cloud: only Alpha → single-supplier category (no savings)
    await _invoice(auth_client, "Alpha", "A-2", "cloud", "1", "100.00")


@pytest.mark.asyncio
async def test_supplier_benchmark_independent(auth_client):
    await _scenario(auth_client)
    rows = (await auth_client.get("/api/v1/analytics/supplier-benchmark")).json()
    assert len(rows) == 2
    by_name = {r["vendor_name"]: r for r in rows}

    alpha = by_name["Alpha"]
    # Alpha: fuel 20.00 + cloud 100.00 = 120.00 over 2 invoices, 2 categories
    assert alpha["total_spend"] == "120.00"
    assert alpha["invoice_count"] == 2
    assert alpha["category_count"] == 2
    assert alpha["avg_invoice"] == "60.00"

    beta = by_name["Beta"]
    assert beta["total_spend"] == "15.00"
    assert beta["category_count"] == 1

    # spend shares of grand total 135.00
    assert alpha["spend_share"] == "88.9"
    assert beta["spend_share"] == "11.1"
    # sorted by spend desc
    assert rows[0]["vendor_name"] == "Alpha"


@pytest.mark.asyncio
async def test_combined_benchmark_prices_and_savings(auth_client):
    await _scenario(auth_client)
    data = (await auth_client.get("/api/v1/analytics/combined-benchmark")).json()

    summary = data["summary"]
    assert summary["supplier_count"] == 2
    assert summary["categories_analyzed"] == 2
    assert summary["multi_supplier_categories"] == 1  # only 'fuel' has >1 supplier
    # Alpha overpays fuel: (2.00 - 1.50) * 10 = 5.00
    assert summary["total_savings_opportunity"] == "5.00"

    cats = {c["category"]: c for c in data["categories"]}
    fuel = cats["fuel"]
    assert fuel["supplier_count"] == 2
    assert fuel["combined_avg_unit"] == "1.7500"   # 35 / 20
    assert fuel["cheapest_vendor_name"] == "Beta"
    assert fuel["cheapest_unit"] == "1.5000"
    assert fuel["savings_opportunity"] == "5.00"

    sup = {s["vendor_name"]: s for s in fuel["suppliers"]}
    assert sup["Beta"]["is_cheapest"] is True
    assert sup["Beta"]["overspend_vs_cheapest"] == "0.00"
    assert sup["Alpha"]["overspend_vs_cheapest"] == "5.00"
    # deviation vs combined avg 1.75: Alpha +14.3%, Beta -14.3%
    assert sup["Alpha"]["deviation_pct"] == "14.3"
    assert sup["Beta"]["deviation_pct"] == "-14.3"

    # single-supplier category has no savings
    cloud = cats["cloud"]
    assert cloud["supplier_count"] == 1
    assert cloud["savings_opportunity"] == "0.00"


@pytest.mark.asyncio
async def test_benchmark_empty_tenant(auth_client):
    rows = (await auth_client.get("/api/v1/analytics/supplier-benchmark")).json()
    assert rows == []
    data = (await auth_client.get("/api/v1/analytics/combined-benchmark")).json()
    assert data["summary"]["total_savings_opportunity"] == "0.00"
    assert data["categories"] == []
