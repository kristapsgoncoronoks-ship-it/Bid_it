"""Tests for the official/open-data market-price scraper (market_prices.py)."""
import importlib

import pytest


class _Resp:
    def __init__(self, text):
        self.text = text
    def raise_for_status(self):
        pass


@pytest.fixture()
def mp(tmp_path, monkeypatch):
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    monkeypatch.setattr(pricing_intelligence, "DB", str(tmp_path / "pi.db"))
    import market_prices
    importlib.reload(market_prices)
    # market_prices imported pricing_intelligence at module load; point it at our reload
    monkeypatch.setattr(market_prices, "pricing_intelligence", pricing_intelligence)
    return market_prices, pricing_intelligence


def test_normalize_and_parse(mp):
    market_prices, _ = mp
    assert market_prices.normalize_country("BE") == "Belgium"
    assert market_prices.normalize_country("pl") == "Poland"
    rows = market_prices.parse_json('[{"country":"BE","date":"2026-05-26","net_price":1.31}]')
    assert rows[0]["country"] == "BE" and rows[0]["net_price"] == 1.31
    csv_rows = market_prices.parse_csv("country,date,net_price\nPL,2026-05-26,1.22\n")
    assert csv_rows[0]["country"] == "PL"


def test_norm_rows_filters_non_diesel_and_bad(mp):
    market_prices, _ = mp
    raw = [{"country": "BE", "date": "2026-05-26", "net_price": 1.31, "product_group": "Diesel"},
           {"country": "BE", "date": "2026-05-26", "net_price": 1.55, "product_group": "Petrol"},
           {"country": "X", "date": "2026-05-26", "net_price": 0}]
    out = market_prices._norm_rows(raw, "test")
    assert len(out) == 1 and out[0]["country"] == "Belgium"


def test_fetch_and_store_into_wholesale(mp, monkeypatch):
    market_prices, pricing_intelligence = mp
    sample = '[{"country":"BE","date":"2026-05-26","net_price":1.31},' \
             '{"country":"PL","date":"2026-05-26","net_price":1.22}]'
    monkeypatch.setenv("MARKET_JSON_URL", "https://example/api")
    monkeypatch.setattr(market_prices.requests, "get", lambda *a, **k: _Resp(sample))
    info = market_prices.fetch_and_store(sources=["json"])
    assert info["rows"] == 2
    assert set(info["countries"]) == {"Belgium", "Poland"}
    con = pricing_intelligence.connect()
    n = con.execute("SELECT COUNT(*) FROM wholesale_prices").fetchone()[0]
    con.close()
    assert n == 2


def test_all_sources_failed_is_clean(mp, monkeypatch):
    market_prices, _ = mp
    monkeypatch.delenv("MARKET_JSON_URL", raising=False)
    monkeypatch.delenv("MARKET_CSV_URL", raising=False)
    with pytest.raises(RuntimeError) as ei:
        market_prices.fetch_and_store()
    assert "all market-price sources failed" in str(ei.value)


def test_pricing_market_route_graceful(client, monkeypatch):
    monkeypatch.delenv("MARKET_JSON_URL", raising=False)
    monkeypatch.delenv("MARKET_CSV_URL", raising=False)
    import re
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/pricing").get_data(as_text=True)).group(1)
    r = client.post("/pricing/market", data={"_csrf": tok})
    assert r.status_code == 200
    assert "Could not scrape market prices" in r.get_data(as_text=True)
