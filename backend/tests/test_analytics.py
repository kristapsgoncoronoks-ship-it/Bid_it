import pytest


async def _make(auth_client, vendor, number, date_, unit, status="paid", currency=None):
    body = {
        "vendor_name": vendor,
        "invoice_number": number,
        "issue_date": date_,
        "status": status,
        "line_items": [
            {
                "description": "svc",
                "category": "cloud",
                "quantity": "1",
                "unit_price": unit,
                "tax_rate": "0",
            },
        ],
    }
    if currency:
        body["currency"] = currency
    return await auth_client.post("/api/v1/invoices", json=body)


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

    # C1.7/WO-24: these four endpoints now return {currency, available_currencies,
    # rows} — a wire-shape change coordinated with the currency-filter fix (a
    # bare list had no way to say WHICH currency the sums below were scoped
    # to). Values are unchanged; only the JSON path grew a `rows` prefix.
    sot = (await auth_client.get("/api/v1/analytics/spend-over-time")).json()
    assert sot["currency"] == "EUR"
    assert sot["available_currencies"] == ["EUR"]
    periods = {b["period"]: b["total"] for b in sot["rows"]}
    assert periods["2026-01"] == "100.00"
    assert periods["2026-02"] == "250.00"

    tv = (await auth_client.get("/api/v1/analytics/top-vendors")).json()
    assert tv["rows"][0]["vendor_name"] == "AWS"
    assert tv["rows"][0]["total"] == "300.00"

    cat = (await auth_client.get("/api/v1/analytics/by-category")).json()
    assert cat["rows"][0]["category"] == "cloud"
    assert cat["rows"][0]["total"] == "350.00"

    st = {
        b["status"]: b["total"]
        for b in (await auth_client.get("/api/v1/analytics/by-status")).json()["rows"]
    }
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


# --------------------------------------------------------------------------- #
# C1.7/WO-24 — no report ever blends currencies into one misleading total.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_mixed_currency_summary_never_blends(auth_client):
    await _make(auth_client, "AWS", "E-1", "2026-01-10", "100.00", currency="EUR")
    await _make(auth_client, "AWS", "E-2", "2026-01-11", "50.00", currency="EUR")
    await _make(auth_client, "Amazon", "U-1", "2026-01-12", "999.00", currency="USD")

    s = (await auth_client.get("/api/v1/analytics/summary")).json()
    # EUR is the most-used currency (2 invoices vs 1) — it wins, and the USD
    # invoice is surfaced, never summed in.
    assert s["currency"] == "EUR"
    assert s["total_spend"] == "150.00"  # 100 + 50, NEVER + 999
    assert s["total_invoices"] == 2
    assert set(s["available_currencies"]) == {"EUR", "USD"}

    usd = (await auth_client.get("/api/v1/analytics/summary?currency=USD")).json()
    assert usd["currency"] == "USD"
    assert usd["total_spend"] == "999.00"
    assert usd["total_invoices"] == 1


@pytest.mark.asyncio
async def test_mixed_currency_top_vendors_and_by_category_never_blend(auth_client):
    await _make(auth_client, "AWS", "E-1", "2026-01-10", "100.00", currency="EUR")
    await _make(auth_client, "Amazon", "U-1", "2026-01-12", "999.00", currency="USD")

    tv = (await auth_client.get("/api/v1/analytics/top-vendors")).json()
    assert tv["currency"] == "EUR"
    names = {r["vendor_name"] for r in tv["rows"]}
    assert names == {"AWS"}  # the USD vendor never appears in the EUR-scoped list

    cat = (await auth_client.get("/api/v1/analytics/by-category")).json()
    assert cat["currency"] == "EUR"
    assert cat["rows"][0]["total"] == "100.00"  # never 1099.00

    cat_usd = (await auth_client.get("/api/v1/analytics/by-category?currency=USD")).json()
    assert cat_usd["rows"][0]["total"] == "999.00"


@pytest.mark.asyncio
async def test_by_dimension_currency_fields_present(auth_client):
    await _make(auth_client, "AWS", "E-1", "2026-01-10", "100.00", currency="EUR")
    d = (await auth_client.get("/api/v1/analytics/by-dimension?dimension=cost_center")).json()
    assert d["currency"] == "EUR"
    assert d["available_currencies"] == ["EUR"]
