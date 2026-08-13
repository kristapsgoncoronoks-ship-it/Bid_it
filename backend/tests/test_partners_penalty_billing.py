"""Late-payment interest may be charged once per day of lateness, and only in
the currency it was incurred in.

Two defects, both reproduced before they were fixed:

* Generating a penalty invoice twice produced two invoices for the same days —
  €73.32 of interest billed as €146.64. Nothing recorded that any interest had
  been billed, so every generation re-accrued from the due date. Billing monthly
  multiplied it.
* `penalty_summary` added every overdue invoice's interest into one total and
  labelled it with whichever row the database returned last, on a query with no
  `ORDER BY`: 73.32 EUR + 73.32 USD came back as "146.64 USD".
"""

from __future__ import annotations

import pytest

from tests.test_partners import _activate, _invoice, _partner


async def _signed_partner(auth_client, **kw):
    p = await _partner(auth_client, requires_contract=True, penalty_enabled=True, **kw)
    doc = (
        await auth_client.post(
            f"/api/v1/partners/{p['id']}/documents",
            json={"kind": "contract", "title": "Contract"},
        )
    ).json()
    await auth_client.post(f"/api/v1/partners/{p['id']}/documents/{doc['id']}/sign", json={})
    return p


async def _overdue(auth_client, partner_id, price="1000.00", currency=None):
    body = _invoice(partner_id, price, issue="2025-12-01", due="2026-01-01")
    if currency:
        body["currency"] = currency
    return (await auth_client.post("/api/v1/issued", json=body)).json()


# --- the same days must not be billed twice ------------------------------------


@pytest.mark.asyncio
async def test_interest_cannot_be_billed_twice_for_the_same_days(auth_client):
    await _activate(auth_client)
    p = await _signed_partner(auth_client, penalty_rate="12")
    await _overdue(auth_client, p["id"])

    first = await auth_client.post(f"/api/v1/partners/{p['id']}/penalty-invoice")
    assert first.status_code == 201
    assert float(first.json()["total"]) > 0

    second = await auth_client.post(f"/api/v1/partners/{p['id']}/penalty-invoice")
    assert second.status_code == 409, (
        f"a second penalty invoice was generated for days already billed: "
        f"{second.json() if second.status_code == 201 else second.text}"
    )


@pytest.mark.asyncio
async def test_the_summary_reports_nothing_billable_straight_after_billing(auth_client):
    await _activate(auth_client)
    p = await _signed_partner(auth_client, penalty_rate="12")
    await _overdue(auth_client, p["id"])

    before = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty")).json()
    assert float(before["total_penalty"]) > 0
    assert before["can_generate"] is True

    await auth_client.post(f"/api/v1/partners/{p['id']}/penalty-invoice")

    after = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty")).json()
    assert float(after["total_penalty"]) == 0.0, "already-billed interest is still billable"
    assert after["can_generate"] is False


@pytest.mark.asyncio
async def test_interest_keeps_accruing_after_it_has_been_billed(auth_client):
    """The stamp must not stop the clock — only move its start. Otherwise the
    fix for double-billing becomes a licence never to bill again."""
    from datetime import date, timedelta

    from app.services import issued_status

    class _Inv:
        currency = "EUR"
        penalty_rate = 12
        total = 1000
        amount_paid = 0
        credited_total = 0
        due_date = date(2026, 1, 1)
        interest_billed_through = None

    inv = _Inv()
    day60 = inv.due_date + timedelta(days=60)
    day90 = inv.due_date + timedelta(days=90)

    assert issued_status.unbilled_days_of(inv, day60) == 60
    inv.interest_billed_through = day60
    assert issued_status.unbilled_days_of(inv, day60) == 0
    assert issued_status.unbilled_days_of(inv, day90) == 30, "the next 30 days are still owed"
    assert issued_status.billable_penalty_of(inv, day90) > 0


@pytest.mark.asyncio
async def test_the_advisory_total_still_shows_the_whole_accrual(auth_client):
    """What is BILLABLE and what has ACCRUED are different questions. Dunning
    tells a debtor what the debt has cost them, which does not shrink because we
    have invoiced part of it."""
    from datetime import date, timedelta

    from app.services import issued_status

    class _Inv:
        currency = "EUR"
        penalty_rate = 12
        total = 1000
        amount_paid = 0
        credited_total = 0
        due_date = date(2026, 1, 1)
        interest_billed_through = date(2026, 3, 2)

    inv = _Inv()
    day90 = inv.due_date + timedelta(days=90)
    assert issued_status.penalty_of(inv, day90) > issued_status.billable_penalty_of(inv, day90)


# --- currencies are never summed -----------------------------------------------


@pytest.mark.asyncio
async def test_a_partner_owing_two_currencies_is_never_summed_into_one(auth_client):
    await _activate(auth_client)
    p = await _signed_partner(auth_client, penalty_rate="12")
    await _overdue(auth_client, p["id"], "1000.00")
    await _overdue(auth_client, p["id"], "1000.00", currency="USD")

    s = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty")).json()

    assert sorted(s["currencies"]) == ["EUR", "USD"]
    assert len(s["lines"]) == 1, "the summary covers one currency, not both"
    # Whatever bucket was chosen, its total must be that bucket alone — never
    # the two added together.
    assert float(s["total_penalty"]) == float(s["lines"][0]["penalty"])


@pytest.mark.asyncio
async def test_each_currency_can_be_summarised_and_billed_on_its_own(auth_client):
    await _activate(auth_client)
    p = await _signed_partner(auth_client, penalty_rate="12")
    await _overdue(auth_client, p["id"], "1000.00")
    await _overdue(auth_client, p["id"], "1000.00", currency="USD")

    eur = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty?currency=EUR")).json()
    usd = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty?currency=USD")).json()
    assert eur["currency"] == "EUR" and usd["currency"] == "USD"
    assert float(eur["total_penalty"]) > 0 and float(usd["total_penalty"]) > 0

    made = await auth_client.post(f"/api/v1/partners/{p['id']}/penalty-invoice?currency=USD")
    assert made.status_code == 201
    assert made.json()["currency"] == "USD"

    # Billing USD must not have consumed the EUR accrual.
    still = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty?currency=EUR")).json()
    assert float(still["total_penalty"]) == float(eur["total_penalty"])
    gone = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty?currency=USD")).json()
    assert float(gone["total_penalty"]) == 0.0


@pytest.mark.asyncio
async def test_the_currency_choice_is_deterministic(auth_client):
    """It used to depend on row order from a query with no ORDER BY. Asked
    repeatedly, the answer must not move."""
    await _activate(auth_client)
    p = await _signed_partner(auth_client, penalty_rate="12")
    await _overdue(auth_client, p["id"], "1000.00")
    await _overdue(auth_client, p["id"], "9000.00", currency="USD")

    seen = set()
    for _ in range(5):
        s = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty")).json()
        seen.add((s["currency"], str(s["total_penalty"])))
    assert len(seen) == 1, f"the default bucket moved between calls: {seen}"
    assert next(iter(seen))[0] == "USD", "the largest accrual is the default"
