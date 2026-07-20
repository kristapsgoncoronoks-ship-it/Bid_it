"""The FX module runs every European currency against the euro."""
import pytest

from app.services import fx

# All non-euro European currencies the module must support.
EUROPEAN = [c.code for c in fx.EUROPEAN_CURRENCIES if c.code != "EUR"]


@pytest.mark.asyncio
async def test_currencies_endpoint_lists_all_european(auth_client):
    r = await auth_client.get("/api/v1/fx/currencies")
    assert r.status_code == 200
    body = r.json()
    assert body["base"] == "EUR" and body["region"] == "europe"
    codes = {c["code"] for c in body["currencies"]}
    # EU/EEA + UK + wider Europe are all present.
    for c in ["EUR", "PLN", "SEK", "DKK", "CHF", "GBP", "RON", "BGN", "HUF", "CZK",
              "NOK", "ISK", "TRY", "RSD", "UAH", "BAM", "MKD", "ALL", "MDL", "GEL"]:
        assert c in codes, f"{c} missing"


@pytest.mark.asyncio
async def test_every_european_currency_converts_to_eur(auth_client):
    # A rate exists (and convert works) for every European currency, incl. the
    # non-ECB ones that ECB never publishes.
    for code in EUROPEAN:
        r = await auth_client.get(f"/api/v1/fx/convert?amount=100&from={code}&to=EUR")
        assert r.status_code == 200, f"{code}: {r.text}"
        assert float(r.json()["converted"]) > 0


@pytest.mark.asyncio
async def test_non_ecb_currencies_flagged_indicative(auth_client):
    body = (await auth_client.get("/api/v1/fx/currencies")).json()
    by = {c["code"]: c for c in body["currencies"]}
    # ECB-published → official (not indicative)
    assert by["PLN"]["ecb"] is True and by["PLN"]["indicative"] is False
    # Wider Europe → indicative
    assert by["RSD"]["ecb"] is False and by["RSD"]["indicative"] is True
    assert by["UAH"]["indicative"] is True


@pytest.mark.asyncio
async def test_convert_between_two_european_currencies(auth_client):
    r = await auth_client.get("/api/v1/fx/convert?amount=1000&from=PLN&to=SEK")
    assert r.status_code == 200
    assert float(r.json()["converted"]) > 0


@pytest.mark.asyncio
async def test_coverage_backfills_missing_currency(db_session):
    from datetime import date

    from sqlalchemy import select
    from app.models.fx import EcbRate

    # RSD is not in the ECB feed; coverage must have seeded an indicative rate.
    rows = list(await db_session.scalars(select(EcbRate).where(EcbRate.currency == "RSD")))
    assert rows, "RSD should be seeded by ensure_european_coverage"
    # Re-running coverage is a no-op (idempotent, additive).
    added = await fx.ensure_european_coverage(db_session, date(2026, 7, 18))
    assert added == 0


@pytest.mark.asyncio
async def test_bulk_resolver_matches_single(db_session):
    """resolve_from (preloaded) must be identical to resolve_rate (per-query)."""
    from datetime import date

    codes = ["PLN", "RSD", "CHF", "EUR", "UAH"]
    series = await fx.load_rate_series(db_session, set(codes))
    for c in codes:
        for d in (date(2026, 7, 1), date(2019, 1, 1), date(2026, 12, 31)):
            single = await fx.resolve_rate(db_session, c, d)
            bulk = fx.resolve_from(series, c, d)
            assert (single is None) == (bulk is None), f"{c} {d}"
            if single is not None:
                assert single.rate == bulk.rate
                assert single.rate_date == bulk.rate_date
                assert single.approximate == bulk.approximate
