"""Coverage for pricing_intelligence.margin_report — focused on the falsy-zero guard
fix (F-B): a legitimate benchmark price of exactly 0.0 is a REAL price, not "no
benchmark". It must NOT be dropped from matched volume, and the gap must still be
computed. None still means "no benchmark"."""
import importlib
import sqlite3

import pytest


@pytest.fixture()
def pi(tmp_path, monkeypatch):
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    fuel = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(pricing_intelligence, "DB", fuel)
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))
    # transactions live in the engine-owned product DB (read read-only by the app).
    prod = sqlite3.connect(fuel)
    prod.execute("""CREATE TABLE IF NOT EXISTS transactions (
        period TEXT, country TEXT, supplier TEXT, station TEXT, date TEXT,
        product_group TEXT, qty REAL, net_eur_eff REAL)""")
    prod.executemany(
        "INSERT INTO transactions (period,country,supplier,station,date,product_group,qty,net_eur_eff)"
        " VALUES (?,?,?,?,?,?,?,?)", [
            # Belgium / Brussels — has a 0.0 my_price + 0.0 wholesale benchmark
            ("2026-05", "Belgium", "BP", "Brussels", "2026-05-10", "Diesel", 1000, 1400.0),  # 1.40 €/L
            # Spain / Madrid — NO benchmark loaded -> my_price must be None
            ("2026-05", "Spain", "MOEVE", "Madrid", "2026-05-09", "Diesel", 500, 650.0),
        ])
    prod.commit(); prod.close()
    return pricing_intelligence


def test_zero_my_price_is_a_real_benchmark_not_dropped(pi):
    # month-grain bucket sample date for 2026-05 is 2026-05-15; store the benchmark there
    pi.load_my_prices([{"country": "Belgium", "city": "Brussels",
                        "date": "2026-05-15", "net_price": 0.0}])
    rows, summ = pi.margin_report("2026-05", "month", "Diesel")
    be = next(r for r in rows if r["country"] == "Belgium")
    # 0.0 is a legitimate price: kept, not coerced to None
    assert be["my_price"] == 0.0
    # gap is computed (eff 1.40 - 0.0 = 1.40), NOT suppressed
    assert be["gap_vs_my"] == 1.40
    assert be["eur_impact"] == round(1.40 * be["qty"], 2)
    # and the 1000 L count as MATCHED volume (would be dropped under `if my`)
    assert summ["matched_litres"] >= 1000


def test_none_my_price_means_no_benchmark(pi):
    # Only Belgium gets a benchmark; Spain has none -> my_price None, counted unmatched
    pi.load_my_prices([{"country": "Belgium", "city": "Brussels",
                        "date": "2026-05-15", "net_price": 0.0}])
    rows, _ = pi.margin_report("2026-05", "month", "Diesel")
    es = next(r for r in rows if r["country"] == "Spain")
    assert es["my_price"] is None
    assert es["gap_vs_my"] is None
    assert es["eur_impact"] is None


def test_zero_wholesale_is_a_real_margin(pi):
    pi.load_wholesale([{"country": "Belgium", "date": "2026-05-15", "net_price": 0.0}])
    rows, _ = pi.margin_report("2026-05", "month", "Diesel")
    be = next(r for r in rows if r["country"] == "Belgium")
    # wholesale 0.0 is a real index value: margin computed (1.40 - 0.0 = 1.40), not None
    assert be["wholesale"] == 0.0
    assert be["margin_vs_wholesale"] == 1.40


def test_pack_avg_is_volume_weighted_not_simple_mean(tmp_path, monkeypatch):
    """FINDINGS pricing #2: gap_vs_pack must compare against the VOLUME-WEIGHTED mean of
    the OTHER suppliers in the cell, not statistics.mean(). A tiny 50 L outlier fill must
    not move the pack as much as a 40,000 L fill. Seed one cell with three suppliers:
    a target plus two competitors at very different volumes."""
    import sqlite3, importlib
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    fuel = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(pricing_intelligence, "DB", fuel)
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))
    prod = sqlite3.connect(fuel)
    prod.execute("""CREATE TABLE IF NOT EXISTS transactions (
        period TEXT, country TEXT, supplier TEXT, station TEXT, date TEXT,
        product_group TEXT, qty REAL, net_eur_eff REAL)""")
    prod.executemany(
        "INSERT INTO transactions (period,country,supplier,station,date,product_group,qty,net_eur_eff)"
        " VALUES (?,?,?,?,?,?,?,?)", [
            # target supplier we read gap_vs_pack for
            ("2026-05", "France", "BP",  "Lille", "2026-05-10", "Diesel",  1000,  1500.0),  # 1.50
            # two OTHER suppliers at wildly different volumes:
            ("2026-05", "France", "TFC", "Lille", "2026-05-11", "Diesel",    50,   100.0),  # 2.00 (50 L outlier)
            ("2026-05", "France", "Q8",  "Lille", "2026-05-12", "Diesel", 40000, 56000.0),  # 1.40 (40,000 L)
        ])
    prod.commit(); prod.close()

    rows, _ = pricing_intelligence.margin_report("2026-05", "month", "Diesel")
    bp = next(r for r in rows if r["supplier"] == "BP")
    # volume-weighted pack of the OTHERS = (50*2.00 + 40000*1.40) / (50 + 40000)
    #   = (100 + 56000) / 40050 = 56100/40050 = 1.40075 -> 1.4007 (4dp HALF_EVEN round)
    # The simple mean would have been (2.00 + 1.40)/2 = 1.70 — and IS WRONG here.
    assert bp["pack_avg"] == 1.4007
    assert bp["pack_avg"] != 1.70
    # gap_vs_pack reflects the weighted pack: 1.50 - 1.4007 = 0.0993
    assert bp["gap_vs_pack"] == 0.0993
