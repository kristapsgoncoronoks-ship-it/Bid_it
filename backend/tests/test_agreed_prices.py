"""Supplier agreed prices + overcharge control (WO-G phase 2).

What must hold:
- the list is validated org configuration (unknown supplier 404s, a broken
  window 400s, re-setting the same window start updates in place);
- window resolution: the agreement in force on a date wins, latest
  `valid_from` breaking overlaps, and an expired window applies to nothing;
- the advisory finding rides the ONE validation engine (code
  `agreed_price_exceeded` appears on capture when AI validation is on);
- the submit gate refuses an overpriced invoice ONLY for orgs that opted
  into `overcharge_block_enabled` — advisory by default;
- the overcharge worklist prices the damage: (unit − agreed) × quantity,
  only for lines above the agreement in force on their invoice date.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import agreed_prices

pytestmark = pytest.mark.asyncio

TODAY = date.today()


async def _org_id(db_session) -> str:
    return await db_session.scalar(select(Organization.id))


async def _vendor(auth_client, name="Overlap Supplies GmbH") -> str:
    r = await auth_client.post("/api/v1/vendors", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _invoice(
    auth_client,
    number: str,
    *,
    unit_price: str,
    quantity: str = "10",
    issue_date: str | None = None,
    description: str = "Packing film roll",
    vendor_name: str = "Overlap Supplies GmbH",
) -> str:
    qty = Decimal(quantity)
    price = Decimal(unit_price)
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": vendor_name,
            "invoice_number": number,
            "issue_date": issue_date or TODAY.isoformat(),
            "currency": "EUR",
            "line_items": [
                {
                    "description": description,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "tax_rate": "0",
                    "amount": str(qty * price),
                }
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _put_agreed(auth_client, vendor_id: str, **kw):
    body = {"vendor_id": vendor_id, "item": "Packing film roll", "agreed_price": "3.20", **kw}
    return await auth_client.put("/api/v1/analytics/supplier-costs/agreed", json=body)


async def test_upsert_validation_and_listing(auth_client):
    vid = await _vendor(auth_client)

    # Unknown supplier 404s (opaque — same as cross-tenant).
    r = await _put_agreed(auth_client, "00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404

    # A window that ends before it starts is refused.
    r = await _put_agreed(
        auth_client,
        vid,
        valid_from=TODAY.isoformat(),
        valid_to=(TODAY - timedelta(days=1)).isoformat(),
    )
    assert r.status_code == 400

    # Create, then re-set the SAME window start: an update, not a duplicate.
    r1 = await _put_agreed(auth_client, vid, item="  Packing FILM roll ", agreed_price="3.20")
    assert r1.status_code == 200, r1.text
    r2 = await _put_agreed(auth_client, vid, item="packing film roll", agreed_price="3.40")
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]

    listed = (await auth_client.get("/api/v1/analytics/supplier-costs/agreed")).json()
    assert len(listed) == 1
    assert listed[0]["item"] == "packing film roll"  # normalised, phase-1 identity
    assert listed[0]["agreed_price"] == "3.40"
    assert listed[0]["vendor_name"] == "Overlap Supplies GmbH"

    gone = await auth_client.delete(f"/api/v1/analytics/supplier-costs/agreed/{r1.json()['id']}")
    assert gone.status_code == 200
    assert (await auth_client.get("/api/v1/analytics/supplier-costs/agreed")).json() == []
    again = await auth_client.delete(f"/api/v1/analytics/supplier-costs/agreed/{r1.json()['id']}")
    assert again.status_code == 404


async def test_active_window_resolution(auth_client, db_session):
    org_id = await _org_id(db_session)
    vid = await _vendor(auth_client)
    jan = TODAY - timedelta(days=200)
    jul = TODAY - timedelta(days=50)

    r = await _put_agreed(
        auth_client,
        vid,
        agreed_price="3.20",
        valid_from=jan.isoformat(),
        valid_to=(jul - timedelta(days=1)).isoformat(),
    )
    assert r.status_code == 200, r.text
    assert (
        await _put_agreed(auth_client, vid, agreed_price="3.60", valid_from=jul.isoformat())
    ).status_code == 200

    async def active(on: date):
        return await agreed_prices.active_price(
            db_session, org_id, vendor_id=vid, item="Packing film roll", currency="EUR", on_date=on
        )

    assert await active(jan + timedelta(days=10)) == Decimal("3.20")
    assert await active(jul + timedelta(days=10)) == Decimal("3.60")
    assert await active(jan - timedelta(days=1)) is None  # before any agreement

    # Overlap: a renegotiation starting inside the open window — latest
    # valid_from wins from its start date.
    assert (
        await _put_agreed(
            auth_client, vid, agreed_price="3.50", valid_from=(jul + timedelta(days=20)).isoformat()
        )
    ).status_code == 200
    assert await active(jul + timedelta(days=25)) == Decimal("3.50")
    assert await active(jul + timedelta(days=5)) == Decimal("3.60")

    # A different currency's agreement never applies.
    assert (
        await agreed_prices.active_price(
            db_session,
            org_id,
            vendor_id=vid,
            item="Packing film roll",
            currency="USD",
            on_date=TODAY,
        )
        is None
    )

    # An EXPIRED window applies to nothing: this item's only agreement ended
    # ten days ago, so today has no agreed price — expiry must actually bite.
    assert (
        await _put_agreed(
            auth_client,
            vid,
            item="Protective floor board",
            agreed_price="12.10",
            valid_from=jan.isoformat(),
            valid_to=(TODAY - timedelta(days=10)).isoformat(),
        )
    ).status_code == 200
    assert (
        await agreed_prices.active_price(
            db_session,
            org_id,
            vendor_id=vid,
            item="Protective floor board",
            currency="EUR",
            on_date=TODAY,
        )
        is None
    )
    assert await agreed_prices.active_price(
        db_session,
        org_id,
        vendor_id=vid,
        item="Protective floor board",
        currency="EUR",
        on_date=TODAY - timedelta(days=30),
    ) == Decimal("12.10")


async def test_advisory_finding_rides_the_validation_engine(auth_client):
    vid = await _vendor(auth_client)
    assert (await _put_agreed(auth_client, vid, agreed_price="3.20")).status_code == 200
    on = await auth_client.put("/api/v1/settings/validation", json={"ai_validation_enabled": True})
    assert on.status_code == 200
    assert on.json()["overcharge_block_enabled"] is False  # advisory by default

    iid = await _invoice(auth_client, "OVER-1", unit_price="3.60")
    inv = (await auth_client.get(f"/api/v1/invoices/{iid}")).json()
    codes = [f["code"] for f in (inv.get("validation_findings") or [])]
    assert "agreed_price_exceeded" in codes, codes

    ok_id = await _invoice(auth_client, "OVER-2", unit_price="3.20")
    inv = (await auth_client.get(f"/api/v1/invoices/{ok_id}")).json()
    codes = [f["code"] for f in (inv.get("validation_findings") or [])]
    assert "agreed_price_exceeded" not in codes, codes


async def test_submit_gate_blocks_only_when_opted_in(auth_client):
    vid = await _vendor(auth_client)
    assert (await _put_agreed(auth_client, vid, agreed_price="3.20")).status_code == 200

    # Default (advisory): the overpriced invoice submits fine.
    iid = await _invoice(auth_client, "GATE-1", unit_price="3.60")
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    assert sub.status_code == 200, sub.text

    # Opt in: the same overcharge now refuses at the gate, named in the error.
    on = await auth_client.put(
        "/api/v1/settings/validation", json={"overcharge_block_enabled": True}
    )
    assert on.status_code == 200 and on.json()["overcharge_block_enabled"] is True
    iid2 = await _invoice(auth_client, "GATE-2", unit_price="3.60")
    sub = await auth_client.post(f"/api/v1/invoices/{iid2}/submit", json={"version": 1})
    assert sub.status_code == 422, sub.text
    assert "agreed" in sub.json()["detail"].lower()

    # Priced at agreement, the gate lets it through.
    iid3 = await _invoice(auth_client, "GATE-3", unit_price="3.20")
    sub = await auth_client.post(f"/api/v1/invoices/{iid3}/submit", json={"version": 1})
    assert sub.status_code == 200, sub.text


async def test_overcharge_worklist_prices_the_damage(auth_client):
    vid = await _vendor(auth_client)
    start = TODAY - timedelta(days=100)
    assert (
        await _put_agreed(auth_client, vid, agreed_price="3.20", valid_from=start.isoformat())
    ).status_code == 200

    # 40 units at 3.60 → 0.40 over × 40 = 16.00 overcharged.
    await _invoice(auth_client, "WL-1", unit_price="3.60", quantity="40")
    # At agreement — not in the worklist.
    await _invoice(auth_client, "WL-2", unit_price="3.20", quantity="40")
    # Overpriced but BEFORE the agreement existed — no agreement in force.
    await _invoice(
        auth_client,
        "WL-3",
        unit_price="9.99",
        quantity="5",
        issue_date=(start - timedelta(days=10)).isoformat(),
    )

    work = (await auth_client.get("/api/v1/analytics/supplier-costs/overcharges")).json()
    assert work["total_overcharge"] == "16.00"
    assert len(work["rows"]) == 1
    row = work["rows"][0]
    assert row["invoice_number"] == "WL-1"
    assert row["agreed_price"] == "3.20"
    assert row["unit_price"] == "3.60"
    assert row["delta_per_unit"] == "0.40"
    assert row["overcharge"] == "16.00"
