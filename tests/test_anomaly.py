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


def test_annotate_in_place_anomaly_discount_and_rebate(tmp_path, monkeypatch):
    import anomaly, sqlite3
    importlib.reload(anomaly)
    # learn the historic Port One-style rebate for (Q8, Belgium): ~0.40 EUR/L
    db = str(tmp_path / "fh.db")
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE transactions (supplier, country, product_group, qty,
                   net_eur, net_eur_eff)""")
    con.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?)", [
        ("Q8", "Belgium", "Diesel", 100, 140.0, 100.0)])   # rebate 0.40/L in history
    con.commit()
    hist = anomaly.expected_rebates(con); con.close()
    assert hist[("Q8", "Belgium")] == 0.40

    rows = [
        # 4 normal Belgian diesel lines ~1.40, plus one HIGH outlier at 2.00
        {"country": "Belgium", "period": "2026-05", "supplier": "TFC", "product_group": "Diesel",
         "qty": 100, "net_eur": 140.0, "net_eur_eff": 140.0, "eurl": 1.40},
        {"country": "Belgium", "period": "2026-05", "supplier": "TFC", "product_group": "Diesel",
         "qty": 100, "net_eur": 141.0, "net_eur_eff": 141.0, "eurl": 1.41},
        {"country": "Belgium", "period": "2026-05", "supplier": "TFC", "product_group": "Diesel",
         "qty": 100, "net_eur": 139.0, "net_eur_eff": 139.0, "eurl": 1.39},
        {"country": "Belgium", "period": "2026-05", "supplier": "TFC", "product_group": "Diesel",
         "qty": 100, "net_eur": 200.0, "net_eur_eff": 200.0, "eurl": 2.00},   # outlier
        # a Q8 line in Belgium with NO rebate applied -> history says ~0.40/L expected
        {"country": "Belgium", "period": "2026-05", "supplier": "Q8", "product_group": "Diesel",
         "qty": 100, "net_eur": 140.0, "net_eur_eff": 140.0, "eurl": 1.40},
        # a separate discount/adjustment line (negative)
        {"country": "Spain", "period": "2026-05", "supplier": "MOEVE", "product_group": "Promo adj",
         "qty": 0, "net_eur": -25.0, "net_eur_eff": -25.0, "eurl": None},
    ]
    ann = anomaly.annotate(rows, hist)
    assert len(ann) == len(rows)                       # 1:1, IN PLACE (no reorder)
    assert ann[3]["anomaly"] and "HIGH outlier" in ann[3]["anomaly"]   # the 2.00 line
    assert ann[0]["anomaly"] is None                    # normal lines not flagged
    assert ann[4]["expected_rebate"] == 40.0            # 0.40/L * 100 L, off-invoice
    assert ann[5]["is_discount"] and "MOEVE" in ann[5]["relates_to"]   # discount related


def test_annotate_shows_applied_rebate():
    import anomaly
    rows = [{"country": "Belgium", "period": "2026-05", "supplier": "Q8",
             "product_group": "Diesel", "qty": 100, "net_eur": 140.0,
             "net_eur_eff": 100.0, "eurl": 1.00}]
    a = anomaly.annotate(rows)[0]
    assert a["rebate"] == 40.0 and not a["is_discount"]   # Port One rebate visible on the line


def test_routing_flag_is_learned_from_spread():
    import app
    # mean 1.05, std-dev 0.05 -> the trigger price is LEARNED from this market's spread
    rows = [{"country": "Germany", "litres": 1000, "eurl": 1.00},
            {"country": "Germany", "litres": 1000, "eurl": 1.10}]
    bench = app._country_benchmarks(rows)
    assert bench["Germany"][0] == 1.05                            # learned mean
    assert app._route_flag(1.00, "Germany", bench) == "PREFER"   # >= 1 sigma below
    assert app._route_flag(1.10, "Germany", bench) == "AVOID"    # >= 1 sigma above
    assert app._route_flag(1.05, "Germany", bench) == ""         # within the spread
    # the SAME absolute price flips meaning when the market itself is higher
    bench_high = app._country_benchmarks([{"country": "Germany", "litres": 1, "eurl": 1.55},
                                          {"country": "Germany", "litres": 1, "eurl": 1.65}])
    assert app._route_flag(1.40, "Germany", bench_high) == "PREFER"   # 1.40 is cheap at a 1.60 market
    # a tight market (no spread) flags nothing — there is no learned outlier
    flat = app._country_benchmarks([{"country": "PL", "litres": 1, "eurl": 1.30},
                                    {"country": "PL", "litres": 1, "eurl": 1.30}])
    assert app._route_flag(1.30, "PL", flat) == ""
