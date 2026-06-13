"""Dynamic client-portal price scraper: encrypted credentials, the demo adapter
loading MY Prices, a custom registered adapter, and graceful failure handling."""
import importlib

import pytest


@pytest.fixture()
def ps(tmp_path, monkeypatch):
    import portal_scraper
    importlib.reload(portal_scraper)
    monkeypatch.setattr(portal_scraper, "DB", str(tmp_path / "portal.db"))
    portal_scraper._SCHEMA_READY.clear()
    # isolate the MY-Prices store the scraper loads into
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    monkeypatch.setattr(pricing_intelligence, "DB", str(tmp_path / "fuel_history.db"))
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))
    return portal_scraper


def test_credentials_encrypted_roundtrip(ps):
    ps.set_credentials("Q8", "JUPITER", "user@x.com", "p@ssw0rd")
    got = ps.get_credentials("Q8", "JUPITER")
    assert got["username"] == "user@x.com" and got["secret"] == "p@ssw0rd"
    # the secret is NOT stored in clear text
    import sqlite3
    raw = sqlite3.connect(ps.DB).execute(
        "SELECT secret_enc FROM portal_credentials").fetchone()[0]
    assert b"p@ssw0rd" not in bytes(raw)


def test_demo_scrape_loads_my_prices(ps):
    import pricing_intelligence as PI
    ps.set_config("DEMO", "demo", config={"rows": [
        {"country": "Germany", "city": "Berlin", "date": "2026-05-10",
         "net_price": 1.41, "product_group": "Diesel"},
        {"country": "Poland", "city": "Warsaw", "date": "2026-05-10", "net_price": 1.33},
    ]})
    res = ps.scrape("DEMO", "JUPITER")
    assert res == {"supplier": "DEMO", "entity": "JUPITER", "fetched": 2, "loaded": 2}
    con = PI.connect()
    rows = con.execute("SELECT country, net_price, source FROM my_prices ORDER BY country").fetchall()
    con.close()
    assert [r["country"] for r in rows] == ["Germany", "Poland"]
    assert all(r["source"] == "portal:DEMO" for r in rows)


def test_custom_adapter_registry(ps, monkeypatch):
    @ps.register("ACME")
    class _Acme(ps.PortalAdapter):
        def fetch(self, creds, cfg, date_from, date_to):
            assert creds["secret"] == "k"        # got the decrypted credential
            return [{"country": "France", "city": "Lyon", "date": "2026-05-01",
                     "net_price": 1.55, "product_group": "Diesel"}]
    ps.set_config("ACME", "custom")
    ps.set_credentials("ACME", "OMUSS", "u", "k")
    assert ps.scrape("ACME", "OMUSS")["loaded"] == 1


def test_normalize_drops_bad_rows(ps):
    rows = ps._normalize([
        {"country": "DE", "city": "X", "date": "2026-05-01", "net_price": 1.4},
        {"country": "", "city": "X", "date": "2026-05-01", "net_price": 1.4},   # no country
        {"country": "PL", "city": "Y", "date": "2026-05-01", "net_price": 0},   # non-positive
        {"country": "ES", "city": "Z", "date": "2026-05-01", "net_price": "x"}, # not a number
    ])
    assert len(rows) == 1 and rows[0]["country"] == "DE"


def test_scrape_records_failed_run_and_raises(ps):
    @ps.register("BOOM")
    class _Boom(ps.PortalAdapter):
        def fetch(self, creds, cfg, date_from, date_to):
            raise RuntimeError("portal down")
    ps.set_config("BOOM", "custom")
    ps.set_credentials("BOOM", "E1", "u", "s")
    with pytest.raises(RuntimeError):
        ps.scrape("BOOM", "E1")
    import sqlite3
    r = sqlite3.connect(ps.DB).execute(
        "SELECT status, message FROM portal_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert r[0] == "failed" and "portal down" in r[1]


def test_unconfigured_portal_raises(ps):
    with pytest.raises(RuntimeError):
        ps.scrape("NOPE", "X")


def test_list_portals_hides_secrets(ps):
    ps.set_config("Q8", "demo")
    ps.set_credentials("Q8", "JUPITER", "user", "secret")
    portals = ps.list_portals()
    assert any(p["supplier"] == "Q8" and p["has_creds"] for p in portals)
    # no secret field anywhere in the listing
    assert "secret" not in str(portals)
