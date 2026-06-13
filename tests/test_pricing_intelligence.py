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
