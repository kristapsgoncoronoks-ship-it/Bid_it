"""Monthly budgeting over received invoices: targets, actuals, over/under, trend."""

from decimal import Decimal

import pytest


async def _activate(auth_client):
    r = await auth_client.put("/api/v1/modules/budget", json={"enabled": True})
    assert r.status_code == 200, r.text


def _bill(number, category, unit, tax, issue="2026-01-10"):
    return {
        "vendor_name": category.title() + " Co",
        "invoice_number": number,
        "issue_date": issue,
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": category,
                "category": category,
                "quantity": "1",
                "unit_price": str(unit),
                "tax_rate": str(tax),
            },
        ],
    }


@pytest.mark.asyncio
async def test_module_gated(auth_client):
    r = await auth_client.get("/api/v1/budget/overview?month=2026-01")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_targets_crud(auth_client):
    await _activate(auth_client)

    r = await auth_client.put(
        "/api/v1/budget/targets", json={"category": "Groceries", "monthly_limit": "250"}
    )
    assert r.status_code == 200
    assert r.json() == {"category": "groceries", "monthly_limit": "250.00"}  # normalized lowercase

    # Upsert (update the same category).
    r = await auth_client.put(
        "/api/v1/budget/targets", json={"category": "groceries", "monthly_limit": "300"}
    )
    assert r.json()["monthly_limit"] == "300.00"

    await auth_client.put(
        "/api/v1/budget/targets", json={"category": "utilities", "monthly_limit": "100"}
    )
    lst = (await auth_client.get("/api/v1/budget/targets")).json()
    assert {t["category"] for t in lst} == {"groceries", "utilities"}

    d = await auth_client.delete("/api/v1/budget/targets/groceries")
    assert d.status_code == 204
    lst2 = (await auth_client.get("/api/v1/budget/targets")).json()
    assert {t["category"] for t in lst2} == {"utilities"}

    missing = await auth_client.delete("/api/v1/budget/targets/nope")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_overview_budget_vs_actual(auth_client):
    await _activate(auth_client)
    # Received invoices for January 2026.
    await auth_client.post("/api/v1/invoices", json=_bill("G-1", "groceries", 200, 0))
    await auth_client.post("/api/v1/invoices", json=_bill("U-1", "utilities", 100, 10))
    # A December bill that must NOT count toward January.
    await auth_client.post(
        "/api/v1/invoices", json=_bill("G-DEC", "groceries", 999, 0, issue="2025-12-20")
    )

    await auth_client.put(
        "/api/v1/budget/targets", json={"category": "groceries", "monthly_limit": "250"}
    )
    await auth_client.put(
        "/api/v1/budget/targets", json={"category": "utilities", "monthly_limit": "100"}
    )

    ov = (await auth_client.get("/api/v1/budget/overview?month=2026-01")).json()
    assert ov["month"] == "2026-01"
    assert ov["total_budget"] == "350.00"
    assert ov["total_actual"] == "310.00"  # 200 groceries + 110 utilities (gross incl. VAT)
    assert ov["total_remaining"] == "40.00"
    assert ov["over_budget"] is False

    rows = {r["category"]: r for r in ov["rows"]}
    assert rows["groceries"]["actual"] == "200.00"
    assert rows["groceries"]["remaining"] == "50.00"
    assert rows["groceries"]["pct"] == 80
    assert rows["groceries"]["over"] is False

    assert rows["utilities"]["actual"] == "110.00"  # 100 + 10% VAT
    assert rows["utilities"]["pct"] == 110
    assert rows["utilities"]["over"] is True
    assert rows["utilities"]["remaining"] == "-10.00"

    # 6-month trend, chronological, ending on the requested month.
    assert len(ov["trend"]) == 6
    assert ov["trend"][-1]["month"] == "2026-01"
    assert ov["trend"][-1]["actual"] == "310.00"
    assert ov["trend"][-1]["budget"] == "350.00"
    # December spend surfaces in the prior-month trend point.
    dec = next(p for p in ov["trend"] if p["month"] == "2025-12")
    assert dec["actual"] == "999.00"


@pytest.mark.asyncio
async def test_untargeted_spend_and_empty_month(auth_client):
    await _activate(auth_client)
    # Spend in a category with no budget target.
    await auth_client.post("/api/v1/invoices", json=_bill("F-1", "fun", 40, 0))

    ov = (await auth_client.get("/api/v1/budget/overview?month=2026-01")).json()
    fun = next(r for r in ov["rows"] if r["category"] == "fun")
    assert fun["untargeted"] is True
    assert fun["budget"] == "0.00"
    assert fun["actual"] == "40.00"
    assert fun["pct"] is None
    assert fun["over"] is False

    # A month with no invoices → zero actuals, empty rows.
    empty = (await auth_client.get("/api/v1/budget/overview?month=2026-03")).json()
    assert empty["total_actual"] == "0.00"
    assert empty["rows"] == []


@pytest.mark.asyncio
async def test_invalid_month(auth_client):
    await _activate(auth_client)
    r = await auth_client.get("/api/v1/budget/overview?month=nonsense")
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# C1.7/WO-24 — an invoice that cannot be converted to EUR is EXCLUDED from the
# actual, never silently counted at a guessed 1:1 parity.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unknown_fx_invoice_excluded_not_guessed(auth_client):
    await _activate(auth_client)
    # A normal EUR bill — must still count fully.
    await auth_client.post("/api/v1/invoices", json=_bill("G-1", "groceries", 200, 0))
    # "QQQ" is not a real currency — no ECB/fallback rate is ever cached for
    # it (same fixture `test_money_invariants.py::
    # test_expense_path_unknown_currency_refuses_never_guesses` uses), so
    # `fx_source` resolves to "unknown" and `total_eur` stays NULL.
    unknown_bill = _bill("Q-1", "groceries", 500, 0)
    unknown_bill["currency"] = "QQQ"
    r = await auth_client.post("/api/v1/invoices", json=unknown_bill)
    assert r.status_code == 201
    assert r.json()["fx_source"] == "unknown"
    assert r.json()["total_eur"] is None

    ov = (await auth_client.get("/api/v1/budget/overview?month=2026-01")).json()
    # The old fallback would have counted the 500 QQQ "as if EUR", giving 700.
    # Correct behaviour: the QQQ invoice is excluded, actual stays at 200.
    assert ov["total_actual"] == "200.00"
    rows = {r["category"]: r for r in ov["rows"]}
    assert rows["groceries"]["actual"] == "200.00"
    assert ov["excluded_unconverted"] == 1


@pytest.mark.asyncio
async def test_known_fx_invoice_still_converts(auth_client):
    await _activate(auth_client)
    # USD is seeded with a fallback ECB-style rate in every test (conftest's
    # ensure_seed_rates/ensure_european_coverage) — a resolvable rate, so this
    # invoice must still be CONVERTED and counted, not excluded.
    usd_bill = _bill("U-1", "groceries", 100, 0)
    usd_bill["currency"] = "USD"
    r = await auth_client.post("/api/v1/invoices", json=usd_bill)
    assert r.status_code == 201
    assert r.json()["fx_source"] in ("ecb", "stated")
    assert r.json()["total_eur"] is not None

    ov = (await auth_client.get("/api/v1/budget/overview?month=2026-01")).json()
    assert ov["excluded_unconverted"] == 0
    assert Decimal(ov["total_actual"]) > 0
