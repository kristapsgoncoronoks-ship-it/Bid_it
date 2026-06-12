"""Anomaly detection respects real price volatility: a market-wide move is NOT an
anomaly; only a supplier diverging from the market is. Routing flags are relative."""
import importlib
import sqlite3


def _seed(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE transactions (period, supplier, country, station, vehicle,
                   date, product_group, qty, net_eur_eff)""")
    rows = []
    # April: everyone at ~1.40 EUR/L in Germany
    for sup in ("BP", "DKV", "E100"):
        rows.append(("2026-04", sup, "Germany", sup + "-stn", "V1", "2026-04-10", "Diesel", 1000, 1400.0))
    # May: the whole MARKET drops ~29% to ~1.00 — normal volatility, NOT an anomaly...
    rows.append(("2026-05", "BP",  "Germany", "BP-stn",  "V1", "2026-05-10", "Diesel", 1000, 1000.0))
    rows.append(("2026-05", "DKV", "Germany", "DKV-stn", "V1", "2026-05-10", "Diesel", 1000, 1000.0))
    # ...except E100, which drops to 0.70 (-50%), diverging from the -29% market move
    rows.append(("2026-05", "E100", "Germany", "E100-stn", "V1", "2026-05-10", "Diesel", 1000, 700.0))
    con.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()


def test_market_wide_move_is_not_flagged(tmp_path, monkeypatch):
    import anomaly
    importlib.reload(anomaly)
    db = str(tmp_path / "fh.db"); _seed(db)
    monkeypatch.setattr(anomaly, "DB", db)
    flags = anomaly.find("2026-05")
    divergences = [f for f in flags if f[0] == "price_divergence"]
    # BP and DKV moved with the market (-29%) -> not flagged; only E100 (-50%) diverged
    assert all("E100" in f[2] for f in divergences)
    assert any("E100" in f[2] for f in divergences)
    assert not any("BP" in f[2] for f in divergences)
    assert not any("DKV" in f[2] for f in divergences)


def test_no_absolute_price_threshold(tmp_path, monkeypatch):
    # even when ALL prices are far from the old 1.40-1.62 band, a uniform market move
    # produces no price anomaly (the thresholds are gone).
    import anomaly
    importlib.reload(anomaly)
    con = sqlite3.connect(str(tmp_path / "fh.db"))
    con.execute("""CREATE TABLE transactions (period, supplier, country, station, vehicle,
                   date, product_group, qty, net_eur_eff)""")
    con.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)", [
        ("2026-04", "BP", "Germany", "s", "V1", "2026-04-10", "Diesel", 1000, 1000.0),
        ("2026-04", "DKV", "Germany", "s2", "V1", "2026-04-10", "Diesel", 1000, 1000.0),
        ("2026-05", "BP", "Germany", "s", "V1", "2026-05-10", "Diesel", 1000, 900.0),   # -10%
        ("2026-05", "DKV", "Germany", "s2", "V1", "2026-05-10", "Diesel", 1000, 900.0)])  # -10%
    con.commit(); con.close()
    monkeypatch.setattr(anomaly, "DB", str(tmp_path / "fh.db"))
    flags = anomaly.find("2026-05")
    assert not any(f[0] == "price_divergence" for f in flags)


def test_routing_flag_is_relative_to_market():
    import app
    rows = [{"country": "Germany", "litres": 1000, "eurl": 1.00},
            {"country": "Germany", "litres": 1000, "eurl": 1.10}]
    bench = app._country_benchmarks(rows)          # ~1.05 volume-weighted
    assert app._route_flag(1.00, "Germany", bench) == "PREFER"   # below market
    assert app._route_flag(1.10, "Germany", bench) == "AVOID"    # above market
    assert app._route_flag(1.05, "Germany", bench) == ""         # at market
    # the SAME absolute price flips meaning when the market itself is higher
    bench_high = app._country_benchmarks([{"country": "Germany", "litres": 1, "eurl": 1.60}])
    assert app._route_flag(1.40, "Germany", bench_high) == "PREFER"   # 1.40 is now cheap
