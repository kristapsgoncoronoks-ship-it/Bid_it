"""FX conversion and ECB comparison.

The bundled fallback seeds a small wobble per month; 2026-06-01 lands on factor
1.0, so USD there is exactly the base 1.0850 — which makes the arithmetic below
exact and easy to verify.
"""

import pytest


def _usd_invoice(number, unit, fx_rate=None, issue="2026-06-01"):
    payload = {
        "vendor_name": "Globex US",
        "invoice_number": number,
        "issue_date": issue,
        "currency": "USD",
        "status": "pending",
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
    if fx_rate is not None:
        payload["fx_rate"] = fx_rate
    return payload


@pytest.mark.asyncio
async def test_rates_and_convert(auth_client):
    rates = (await auth_client.get("/api/v1/fx/rates?on=2026-06-15")).json()
    ccys = {r["currency"]: r for r in rates["rates"]}
    assert "USD" in ccys and "GBP" in ccys
    assert ccys["USD"]["rate"] == "1.085000"  # 2026-06-01 on-or-before, factor 1.0

    # 108.50 USD → 100.00 EUR at 1.0850
    conv = (
        await auth_client.get("/api/v1/fx/convert?amount=108.50&from=USD&to=EUR&on=2026-06-10")
    ).json()
    assert conv["converted"] == "100.00"
    assert conv["to_currency"] == "EUR"

    # unknown currency → 404
    bad = await auth_client.get("/api/v1/fx/convert?amount=1&from=XXX&to=EUR")
    assert bad.status_code == 404


@pytest.mark.asyncio
async def test_eur_invoice_is_1to1(auth_client):
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Acme",
            "invoice_number": "E-1",
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "x",
                    "category": "c",
                    "quantity": "1",
                    "unit_price": "100.00",
                    "tax_rate": "0",
                }
            ],
        },
    )
    inv = r.json()
    assert inv["total_eur"] == "100.00"
    assert inv["fx_source"] == "eur"


@pytest.mark.asyncio
async def test_foreign_invoice_converts_at_ecb(auth_client):
    # No stated rate → convert at ECB (1.0850). 108.50 USD → 100.00 EUR.
    r = await auth_client.post("/api/v1/invoices", json=_usd_invoice("U-ECB", "108.50"))
    inv = r.json()
    assert inv["currency"] == "USD"
    assert inv["total"] == "108.50"
    assert inv["total_eur"] == "100.00"
    assert inv["fx_source"] == "ecb"


@pytest.mark.asyncio
async def test_stated_rate_markup_vs_ecb(auth_client):
    # Supplier stated a punitive 1.00 rate; ECB is 1.0850.
    await auth_client.post(
        "/api/v1/invoices", json=_usd_invoice("U-STATED", "108.50", fx_rate="1.00")
    )

    data = (await auth_client.get("/api/v1/fx/ecb-comparison")).json()
    s = data["summary"]
    assert s["non_eur_invoices"] == 1
    assert s["with_stated_rate"] == 1
    assert s["currencies"] == ["USD"]
    # stated EUR 108.50 vs ECB EUR 100.00 → 8.50 markup
    assert s["total_eur_at_ecb"] == "100.00"
    assert s["total_markup_eur"] == "8.50"

    row = data["rows"][0]
    assert row["eur_at_ecb"] == "100.00"
    assert row["eur_at_stated"] == "108.50"
    assert row["markup_eur"] == "8.50"
    # (1.00 - 1.0850)/1.0850 * 100 = -7.8%
    assert row["deviation_pct"] == "-7.8"


# --------------------------------------------------------------------------- #
# WO-8 characterisation: the invoice path IS the canonical convention. These
# pins were written BEFORE the expense path was fixed; the expense path must
# converge on exactly these figures (see test_money_invariants FI-15 suite).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ecb_convention_divides(db_session):
    """ECB publishes units of `currency` per 1 EUR; EUR = amount / rate."""
    from datetime import date
    from decimal import Decimal

    from app.services import fx

    await fx.load_rates(db_session, [(date(2026, 7, 15), "ZZZ", Decimal("2.5"))])
    eur, resolved = await fx.to_eur(db_session, Decimal("100.00"), "ZZZ", date(2026, 7, 15))
    assert eur == Decimal("40.00")  # 100 / 2.5 — NEVER 100 × 2.5
    assert resolved is not None and resolved.rate == Decimal("2.5")


@pytest.mark.asyncio
async def test_invoice_path_characterisation_matrix(auth_client):
    """Golden figures the invoice create path produces today (2026-06-01 rates,
    wobble factor 1.0). The expense path must yield these exact EUR values."""
    cases = [
        ("USD", "108.50", "100.00"),  # 1.0850 USD per EUR
        ("PLN", "430.00", "100.00"),  # 4.3000 PLN per EUR
        ("CZK", "253.00", "10.00"),  # 25.300 CZK per EUR
    ]
    for i, (ccy, unit, expected_eur) in enumerate(cases):
        payload = _usd_invoice(f"CHAR-{i}", unit, issue="2026-06-05")
        payload["currency"] = ccy
        inv = (await auth_client.post("/api/v1/invoices", json=payload)).json()
        assert inv["total_eur"] == expected_eur, (ccy, inv)
        assert inv["fx_source"] == "ecb"


@pytest.mark.asyncio
async def test_rate_selection_uses_most_recent_on_or_before(db_session):
    """A Sunday transaction uses Friday's rate (ECB publishes no weekend rate)."""
    from datetime import date
    from decimal import Decimal

    from app.services import fx

    await fx.load_rates(
        db_session,
        [
            (date(2026, 7, 13), "ZZY", Decimal("2.0")),  # Monday
            (date(2026, 7, 17), "ZZY", Decimal("4.0")),  # Friday
        ],
    )
    r = await fx.resolve_rate(db_session, "ZZY", date(2026, 7, 19))  # Sunday
    assert r is not None
    assert r.rate == Decimal("4.0") and r.rate_date == date(2026, 7, 17)
    assert r.approximate is False


@pytest.mark.asyncio
async def test_missing_rate_yields_none_not_a_guess(db_session):
    from datetime import date
    from decimal import Decimal

    from app.services import fx

    eur, resolved = await fx.to_eur(db_session, Decimal("99.99"), "QQX", date(2026, 7, 15))
    assert eur is None and resolved is None


@pytest.mark.asyncio
async def test_refresh_owner_only_and_graceful(auth_client):
    # auth_client is the workspace owner; refresh is allowed and must not raise
    # even when the ECB host is unreachable (returns ok=false).
    r = await auth_client.post("/api/v1/fx/refresh")
    assert r.status_code == 200
    assert r.json()["ok"] in (True, False)
