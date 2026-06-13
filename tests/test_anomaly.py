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


def test_annotate_identical_after_stats_hoist():
    """Guard for the median/MAD hoist: annotate() must produce a byte-identical result
    to a per-row reference that recomputes median+MAD inline (the pre-refactor behavior).
    Mixed buckets (multi-row, MAD>0; identical-values, MAD==0; <3 rows; empty/None)."""
    import anomaly, statistics, collections

    def reference(rows, hist_rebates=None):
        hist_rebates = hist_rebates or {}
        buckets = collections.defaultdict(list)
        for r in rows:
            if r.get("product_group") == "Diesel" and (r.get("qty") or 0) > 0 and r.get("eurl"):
                buckets[(r.get("country"), r.get("period"))].append(r["eurl"])
        out = []
        for r in rows:
            net = r.get("net_eur") or 0
            eff = r.get("net_eur_eff")
            rebate = (net - eff) if eff is not None else 0.0
            pg = r.get("product_group")
            is_discount = (net < 0) or (pg in anomaly.DISCOUNT_GROUPS)
            anomaly_s = relates_to = expected_rebate = None
            if is_discount:
                relates_to = f"{r.get('supplier','')} {r.get('country','')} {r.get('period') or ''}".strip()
            sample = buckets.get((r.get("country"), r.get("period")), [])
            if pg == "Diesel" and (r.get("qty") or 0) > 0 and r.get("eurl"):
                # recompute median+MAD PER ROW (old hot path)
                if len(sample) >= 3:
                    med = statistics.median(sample)
                    mad = statistics.median([abs(x - med) for x in sample])
                    if mad > 0:
                        z = 0.6745 * (r["eurl"] - med) / mad; thr = 3.5
                        flag = "HIGH" if z > thr else "LOW" if z < -thr else None
                    else:
                        sd = statistics.pstdev(sample)
                        if sd <= 0:
                            flag = None
                        else:
                            z = (r["eurl"] - med) / sd; thr = anomaly.ANOMALY_SIGMAS
                            flag = "HIGH" if z > thr else "LOW" if z < -thr else None
                    if flag:
                        med2 = statistics.median(sample)
                        tag = ((r.get("country") or "") + " " + (r.get("period") or "")).strip()
                        anomaly_s = f"{r['eurl']:.3f} EUR/L — {flag} outlier vs {tag} median {med2:.3f}"
            exp = hist_rebates.get((r.get("supplier"), r.get("country")))
            if exp and abs(rebate) < 0.005 and pg == "Diesel" and (r.get("qty") or 0) > 0:
                expected_rebate = exp * r["qty"]
            out.append({"anomaly": anomaly_s, "rebate": rebate, "is_discount": is_discount,
                        "relates_to": relates_to, "expected_rebate": expected_rebate})
        return out

    rows = [
        # bucket A (Germany 05): MAD>0, one HIGH outlier
        {"country": "Germany", "period": "2026-05", "supplier": "BP", "product_group": "Diesel",
         "qty": 100, "net_eur": 140.0, "net_eur_eff": 140.0, "eurl": 1.40},
        {"country": "Germany", "period": "2026-05", "supplier": "BP", "product_group": "Diesel",
         "qty": 100, "net_eur": 141.0, "net_eur_eff": 141.0, "eurl": 1.41},
        {"country": "Germany", "period": "2026-05", "supplier": "BP", "product_group": "Diesel",
         "qty": 100, "net_eur": 139.0, "net_eur_eff": 139.0, "eurl": 1.39},
        {"country": "Germany", "period": "2026-05", "supplier": "BP", "product_group": "Diesel",
         "qty": 100, "net_eur": 250.0, "net_eur_eff": 250.0, "eurl": 2.50},
        # bucket B (Poland 05): all identical -> MAD==0, sd==0 -> nothing flagged
        {"country": "Poland", "period": "2026-05", "supplier": "DKV", "product_group": "Diesel",
         "qty": 100, "net_eur": 130.0, "net_eur_eff": 130.0, "eurl": 1.30},
        {"country": "Poland", "period": "2026-05", "supplier": "DKV", "product_group": "Diesel",
         "qty": 100, "net_eur": 130.0, "net_eur_eff": 130.0, "eurl": 1.30},
        {"country": "Poland", "period": "2026-05", "supplier": "DKV", "product_group": "Diesel",
         "qty": 100, "net_eur": 130.0, "net_eur_eff": 130.0, "eurl": 1.30},
        # bucket C (Spain 05): <3 rows -> no stats
        {"country": "Spain", "period": "2026-05", "supplier": "MOEVE", "product_group": "Diesel",
         "qty": 100, "net_eur": 120.0, "net_eur_eff": 120.0, "eurl": 1.20},
        {"country": "Spain", "period": "2026-05", "supplier": "MOEVE", "product_group": "Diesel",
         "qty": 100, "net_eur": 122.0, "net_eur_eff": 122.0, "eurl": 1.22},
        # non-diesel / discount line -> no bucket
        {"country": "Spain", "period": "2026-05", "supplier": "MOEVE", "product_group": "Promo adj",
         "qty": 0, "net_eur": -25.0, "net_eur_eff": -25.0, "eurl": None},
    ]
    hist = {("Q8", "Belgium"): 0.40}
    assert anomaly.annotate([dict(r) for r in rows], hist) == reference([dict(r) for r in rows], hist)
    # and the flagged line is the 2.50 outlier, exact string preserved
    ann = anomaly.annotate([dict(r) for r in rows], hist)
    assert ann[3]["anomaly"] == "2.500 EUR/L — HIGH outlier vs Germany 2026-05 median 1.405"
    assert all(a["anomaly"] is None for i, a in enumerate(ann) if i != 3)


def test_robust_outlier_wrapper_matches_flag():
    """The retained _robust_outlier wrapper equals _robust_flag(_robust_stats(...))."""
    import anomaly
    for sample, val in [([1.0, 1.1, 1.05, 2.0], 2.0), ([1.0, 1.0, 1.0], 1.0),
                        ([1.0, 1.1], 1.0), ([], 1.0)]:
        assert anomaly._robust_outlier(val, sample) == \
               anomaly._robust_flag(val, anomaly._robust_stats(sample))


def _seed_tod(path, rows):
    """Seed a transactions table that includes the `time` column for time-of-day tests."""
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE transactions (period, supplier, country, station, vehicle,
                   date, time, product_group, qty, net_eur_eff)""")
    con.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()


def test_off_hours_flag_night_yes_day_no(tmp_path, monkeypatch):
    import anomaly
    importlib.reload(anomaly)
    db = str(tmp_path / "fh.db")
    _seed_tod(db, [
        # 23:30 deep-night diesel -> flagged
        ("2026-05", "BP", "Germany", "s", "V1", "2026-05-10", "23:30", "Diesel", 500, 500.0),
        # 13:00 daytime diesel -> NOT flagged
        ("2026-05", "BP", "Germany", "s", "V2", "2026-05-10", "13:00", "Diesel", 500, 500.0),
    ])
    monkeypatch.setattr(anomaly, "DB", db)
    flags = anomaly.find("2026-05")
    off = [f for f in flags if f[0] == "off_hours"]
    assert len(off) == 1
    assert "V1" in off[0][2] and "23:30" in off[0][2]
    assert not any("V2" in f[2] for f in off)


def test_off_hours_bad_time_does_not_raise_or_flag(tmp_path, monkeypatch):
    import anomaly
    importlib.reload(anomaly)
    db = str(tmp_path / "fh.db")
    _seed_tod(db, [
        ("2026-05", "BP", "Germany", "s", "V1", "2026-05-10", "", "Diesel", 100, 100.0),
        ("2026-05", "BP", "Germany", "s", "V2", "2026-05-10", "xx:yy", "Diesel", 100, 100.0),
        ("2026-05", "BP", "Germany", "s", "V3", "2026-05-10", "25:00", "Diesel", 100, 100.0),
    ])
    monkeypatch.setattr(anomaly, "DB", db)
    flags = anomaly.find("2026-05")   # must not raise
    assert not any(f[0] == "off_hours" for f in flags)


def test_time_of_day_summary_buckets_and_weighted_price(tmp_path, monkeypatch):
    import anomaly
    importlib.reload(anomaly)
    db = str(tmp_path / "fh.db")
    _seed_tod(db, [
        # 08:00 bucket: 100L @1.40 and 100L @1.60 -> vol-weighted 1.50
        ("2026-05", "BP", "Germany", "s", "V1", "2026-05-10", "08:15", "Diesel", 100, 140.0),
        ("2026-05", "BP", "Germany", "s", "V2", "2026-05-11", "08:45", "Diesel", 100, 160.0),
        # 14:00 bucket: single 200L @1.00
        ("2026-05", "BP", "Germany", "s", "V3", "2026-05-12", "14:00", "Diesel", 200, 200.0),
        # bad time -> 'unknown' bucket, not dropped from totals
        ("2026-05", "BP", "Germany", "s", "V4", "2026-05-13", "nope", "Diesel", 50, 50.0),
    ])
    monkeypatch.setattr(anomaly, "DB", db)
    summ = {b["hour"]: b for b in anomaly.time_of_day_summary("2026-05")}
    assert summ["08:00"]["count"] == 2
    assert summ["08:00"]["litres"] == 200
    assert abs(summ["08:00"]["eur_l"] - 1.50) < 1e-9   # volume-weighted NET EUR/L
    assert summ["14:00"]["count"] == 1 and abs(summ["14:00"]["eur_l"] - 1.00) < 1e-9
    assert "unknown" in summ and summ["unknown"]["count"] == 1 and summ["unknown"]["litres"] == 50
    # total count across buckets accounts for every row (none silently dropped)
    assert sum(b["count"] for b in summ.values()) == 4


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
