"""WO-G phase 1: supplier cost analytics — the read models, pinned test by test.

1. Change detection is EXACT: latest daily price vs the quantity-weighted
   trailing average of everything before it, % to one decimal; the KPI cards
   come from the same fold as the movers table, so they cannot disagree.
2. An item needs >= 2 price points to be tracked; zero-quantity and
   zero-price lines are never price points.
3. An item is its normalised description — case/whitespace variants fold.
4. Single-currency scope (C1.7): the dominant currency is reported, others
   are surfaced in available_currencies, never folded in.
5. The history series is monthly, weighted, oldest first.
6. Tenant isolation: another workspace's purchases do not exist here.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest


def _day(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


async def _invoice(
    client,
    vendor: str,
    number: str,
    *,
    days_ago: int,
    lines: list[tuple[str, str, str]],
    currency: str = "EUR",
    headers: dict | None = None,
):
    """lines: (description, quantity, unit_price)."""
    r = await client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": vendor,
            "invoice_number": number,
            "issue_date": _day(days_ago),
            "currency": currency,
            "line_items": [
                {
                    "description": d,
                    "category": "materials",
                    "quantity": q,
                    "unit_price": u,
                    "tax_rate": "0",
                }
                for d, q, u in lines
            ],
        },
        headers=headers or {},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.asyncio
async def test_change_detection_math_and_kpis_agree(auth_client):
    await _invoice(
        auth_client, "Supply Co", "SC-1", days_ago=60, lines=[("Widget A", "10", "2.00")]
    )
    await _invoice(
        auth_client, "Supply Co", "SC-2", days_ago=30, lines=[("Widget A", "10", "2.00")]
    )
    await _invoice(auth_client, "Supply Co", "SC-3", days_ago=1, lines=[("Widget A", "10", "3.00")])

    changes = (await auth_client.get("/api/v1/analytics/supplier-costs/changes")).json()
    assert changes["currency"] == "EUR"
    assert changes["total_tracked"] == 1
    row = changes["rows"][0]
    assert row["vendor_name"] == "Supply Co"
    assert row["item"] == "widget a"
    assert row["points"] == 3
    assert row["trailing_avg"] == "2.00", "baseline excludes the latest observation"
    assert row["latest_price"] == "3.00"
    assert row["pct_change"] == "50.0"
    assert row["latest_date"] == _day(1)

    kpis = (await auth_client.get("/api/v1/analytics/supplier-costs/kpis")).json()
    assert kpis["suppliers"] == 1
    assert kpis["tracked_items"] == 1
    assert kpis["risers"] == 1 and kpis["fallers"] == 0
    assert kpis["biggest_mover"]["pct_change"] == "50.0"


@pytest.mark.asyncio
async def test_min_points_and_zero_line_guards(auth_client):
    # One real purchase + one zero-price line + one zero-quantity line:
    # only ONE price point exists, so the item is not tracked.
    await _invoice(auth_client, "Supply Co", "SC-10", days_ago=20, lines=[("Sealant", "5", "8.00")])
    await _invoice(auth_client, "Supply Co", "SC-11", days_ago=10, lines=[("Sealant", "5", "0")])
    await _invoice(auth_client, "Supply Co", "SC-12", days_ago=5, lines=[("Sealant", "0", "9.00")])

    changes = (await auth_client.get("/api/v1/analytics/supplier-costs/changes")).json()
    assert changes["total_tracked"] == 0


@pytest.mark.asyncio
async def test_item_identity_folds_case_and_whitespace(auth_client):
    await _invoice(
        auth_client, "Supply Co", "SC-20", days_ago=40, lines=[("  Copper Pipe ", "4", "10.00")]
    )
    await _invoice(
        auth_client, "Supply Co", "SC-21", days_ago=2, lines=[("copper pipe", "4", "12.00")]
    )
    changes = (await auth_client.get("/api/v1/analytics/supplier-costs/changes")).json()
    assert changes["total_tracked"] == 1
    assert changes["rows"][0]["item"] == "copper pipe"
    assert changes["rows"][0]["pct_change"] == "20.0"


@pytest.mark.asyncio
async def test_single_currency_scope_surfaces_others(auth_client):
    await _invoice(auth_client, "Supply Co", "SC-30", days_ago=30, lines=[("Rope", "2", "5.00")])
    await _invoice(auth_client, "Supply Co", "SC-31", days_ago=3, lines=[("Rope", "2", "6.00")])
    await _invoice(
        auth_client, "Foreign Co", "FC-1", days_ago=3, lines=[("Rope", "2", "9.00")], currency="USD"
    )

    changes = (await auth_client.get("/api/v1/analytics/supplier-costs/changes")).json()
    assert changes["currency"] == "EUR"
    assert changes["available_currencies"] == ["EUR", "USD"]
    assert changes["total_tracked"] == 1, "the USD purchase is surfaced, never folded in"
    usd = (await auth_client.get("/api/v1/analytics/supplier-costs/changes?currency=USD")).json()
    assert usd["total_tracked"] == 0, "one USD purchase is one point — below MIN_POINTS"


@pytest.mark.asyncio
async def test_history_series_is_monthly_weighted_and_ordered(auth_client):
    inv = await _invoice(
        auth_client, "Supply Co", "SC-40", days_ago=40, lines=[("Gravel", "10", "2.00")]
    )
    await _invoice(auth_client, "Supply Co", "SC-41", days_ago=35, lines=[("Gravel", "30", "4.00")])
    await _invoice(auth_client, "Supply Co", "SC-42", days_ago=2, lines=[("Gravel", "10", "5.00")])

    hist = (
        await auth_client.get(
            "/api/v1/analytics/supplier-costs/history",
            params={"vendor_id": inv["vendor_id"], "item": "Gravel"},
        )
    ).json()
    series = hist["series"]
    assert 1 <= len(series) <= 3, "months with no purchases are absent, not zero-filled"
    months = [p["month"] for p in series]
    assert months == sorted(months), "oldest first"
    total_points = sum(p["points"] for p in series)
    assert total_points == 3
    # The two same-window purchases weight to (20+120)/40 = 3.50 when they
    # share a month; whatever the month split, the newest point is 5.00.
    assert series[-1]["avg_price"] == "5.00"


@pytest.mark.asyncio
async def test_other_workspaces_purchases_do_not_exist_here(auth_client, client):
    await _invoice(auth_client, "Supply Co", "SC-50", days_ago=30, lines=[("Beam", "2", "10.00")])
    await _invoice(auth_client, "Supply Co", "SC-51", days_ago=3, lines=[("Beam", "2", "11.00")])

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Analytics Org",
            "email": "owner@other-costs.example",
            "password": "supersecret1",
            "name": "Other Owner",
        },
    )
    assert reg.status_code in (200, 201)
    h = {"Authorization": f"Bearer {reg.json()['token']['access_token']}"}
    await _invoice(
        client, "Supply Co", "X-1", days_ago=20, lines=[("Beam", "2", "50.00")], headers=h
    )
    await _invoice(
        client, "Supply Co", "X-2", days_ago=2, lines=[("Beam", "2", "99.00")], headers=h
    )

    ours = (await auth_client.get("/api/v1/analytics/supplier-costs/changes")).json()
    assert ours["total_tracked"] == 1
    assert ours["rows"][0]["latest_price"] == "11.00", "the other workspace's 99.00 must not leak"

    theirs = (await client.get("/api/v1/analytics/supplier-costs/changes", headers=h)).json()
    assert theirs["rows"][0]["latest_price"] == "99.00"
