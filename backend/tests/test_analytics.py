import pytest


async def _make(auth_client, vendor, number, date_, unit, status="paid"):
    return await auth_client.post("/api/v1/invoices", json={
        "vendor_name": vendor,
        "invoice_number": number,
        "issue_date": date_,
        "status": status,
        "line_items": [
            {"description": "svc", "category": "cloud", "quantity": "1", "unit_price": unit, "tax_rate": "0"},
        ],
    })


@pytest.mark.asyncio
async def test_summary_and_breakdowns(auth_client):
    await _make(auth_client, "AWS", "A1", "2026-01-10", "100.00", status="paid")
    await _make(auth_client, "AWS", "A2", "2026-02-10", "200.00", status="pending")
    await _make(auth_client, "Slack", "S1", "2026-02-15", "50.00", status="overdue")

    summary = (await auth_client.get("/api/v1/analytics/summary")).json()
    assert summary["total_invoices"] == 3
    assert summary["total_spend"] == "350.00"
    assert summary["vendor_count"] == 2
    # unpaid = pending(200) + overdue(50)
    assert summary["unpaid_amount"] == "250.00"

    sot = (await auth_client.get("/api/v1/analytics/spend-over-time")).json()
    periods = {b["period"]: b["total"] for b in sot}
    assert periods["2026-01"] == "100.00"
    assert periods["2026-02"] == "250.00"

    tv = (await auth_client.get("/api/v1/analytics/top-vendors")).json()
    assert tv[0]["vendor_name"] == "AWS"
    assert tv[0]["total"] == "300.00"

    cat = (await auth_client.get("/api/v1/analytics/by-category")).json()
    assert cat[0]["category"] == "cloud"
    assert cat[0]["total"] == "350.00"

    st = {b["status"]: b["total"] for b in (await auth_client.get("/api/v1/analytics/by-status")).json()}
    assert st["paid"] == "100.00"
    assert st["pending"] == "200.00"
    assert st["overdue"] == "50.00"


@pytest.mark.asyncio
async def test_summary_empty(auth_client):
    s = (await auth_client.get("/api/v1/analytics/summary")).json()
    assert s["total_invoices"] == 0
    assert s["total_spend"] == "0"
    assert s["avg_invoice"] == "0"


@pytest.mark.asyncio
async def test_date_window_filter(auth_client):
    await _make(auth_client, "AWS", "A1", "2026-01-10", "100.00")
    await _make(auth_client, "AWS", "A2", "2026-03-10", "200.00")
    s = (await auth_client.get("/api/v1/analytics/summary?start=2026-02-01&end=2026-12-31")).json()
    assert s["total_spend"] == "200.00"
