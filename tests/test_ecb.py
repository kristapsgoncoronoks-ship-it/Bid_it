"""Tests for the ECB exchange-rate module and the /fx comparison page."""
import importlib

import pytest

SAMPLE_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
 <Cube>
  <Cube time="2026-05-30"><Cube currency="PLN" rate="4.30"/><Cube currency="SEK" rate="10.95"/></Cube>
  <Cube time="2026-05-31"><Cube currency="PLN" rate="4.31"/><Cube currency="SEK" rate="10.97"/></Cube>
 </Cube>
</gesmes:Envelope>'''


@pytest.fixture()
def ecb(tmp_path, monkeypatch):
    import ecb_rates
    importlib.reload(ecb_rates)
    monkeypatch.setattr(ecb_rates, "DB", str(tmp_path / "ecb_test.db"))
    return ecb_rates


def test_parse_namespaced_xml(ecb):
    rows = ecb._parse(SAMPLE_XML)
    assert ("2026-05-31", "PLN", 4.31) in rows
    assert ("2026-05-30", "SEK", 10.95) in rows
    assert len(rows) == 4


def test_rate_for_nearest_prior(ecb):
    con = ecb.connect()
    con.executemany("INSERT OR REPLACE INTO ecb_fx (date,currency,rate) VALUES (?,?,?)",
                    ecb._parse(SAMPLE_XML))
    con.commit(); con.close()
    assert ecb.rate_for("PLN", "2026-05-31") == (4.31, "2026-05-31")
    # a date between/after falls back to the nearest prior business day
    assert ecb.rate_for("PLN", "2026-06-05") == (4.31, "2026-05-31")
    # before any cached date -> none
    assert ecb.rate_for("PLN", "2026-05-01") == (None, None)
    # EUR is always 1.0
    assert ecb.rate_for("EUR", "2026-05-31")[0] == 1.0


def test_fetch_failure_is_clean(ecb, monkeypatch):
    import requests
    def boom(*a, **k):
        raise requests.RequestException("blocked")
    monkeypatch.setattr(ecb.requests, "get", boom)
    with pytest.raises(RuntimeError) as ei:
        ecb.fetch_and_store()
    assert "ECB" in str(ei.value)


def test_fx_page_renders(client):
    r = client.get("/fx")
    assert r.status_code == 200
    assert "Invoice exchange rate vs ECB" in r.get_data(as_text=True)


def test_fx_refresh_is_graceful(client):
    import re
    html = client.get("/fx").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    r = client.post("/fx", data={"_csrf": tok, "__act": "refresh"})
    # whether the ECB is reachable or not, the page must still render 200
    assert r.status_code == 200
    assert "vs ECB" in r.get_data(as_text=True)
