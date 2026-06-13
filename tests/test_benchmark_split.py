"""D3 — benchmark tables (my_prices/wholesale_prices) split OUT of the engine-owned
product DB (fuel_history.db) into the app/portal-owned benchmark.db.

Covers:
 1. writes land in benchmark.db, NOT in the (read-only) product DB;
 2. the margin reader still joins transactions (product, read-only) + prices (benchmark);
 3. the one-time legacy copy from fuel_history.db is idempotent.
"""
import importlib
import sqlite3

import pytest


@pytest.fixture()
def pi(tmp_path, monkeypatch):
    """A reloaded pricing_intelligence pointed at fresh temp DBs: a product DB
    (fuel_history.db, holding `transactions`) and a separate benchmark.db."""
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    fuel = str(tmp_path / "fuel_history.db")
    bench = str(tmp_path / "benchmark.db")
    monkeypatch.setattr(pricing_intelligence, "DB", fuel)
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", bench)
    # Make the dataproduct read-only accessor resolve the same temp product file even
    # when a caller (or the migration) goes through dataproduct.connect("fuel_history").
    import dataproduct
    importlib.reload(dataproduct)
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fuel)
    # The migration guard is keyed by BENCHMARK_DB path; temp paths are unique per
    # test so there is no cross-test contamination, but clear it to be explicit.
    pricing_intelligence._MIGRATED.clear()
    pi = pricing_intelligence
    pi._fuel, pi._bench = fuel, bench
    return pi


def _make_transactions(fuel):
    prod = sqlite3.connect(fuel)
    prod.execute("""CREATE TABLE IF NOT EXISTS transactions (
        period TEXT, country TEXT, station TEXT, supplier TEXT, date TEXT,
        product_group TEXT, qty REAL, net_eur_eff REAL)""")
    prod.executemany(
        "INSERT INTO transactions (period,country,station,supplier,date,product_group,qty,net_eur_eff)"
        " VALUES (?,?,?,?,?,?,?,?)", [
            ("2026-05", "Belgium", "Antwerp", "BP", "2026-05-10", "Diesel", 1000, 1500.0),  # 1.50/L
        ])
    prod.commit(); prod.close()


def _table_exists(path, table):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone() is not None
    finally:
        con.close()


def _count(path, table):
    if not _table_exists(path, table):
        return 0
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


def test_write_lands_in_benchmark_not_product(pi):
    """load_my_prices writes benchmark.db; the FRESH product DB gets NO my_prices."""
    _make_transactions(pi._fuel)
    n = pi.load_my_prices([
        {"country": "Belgium", "city": "Antwerp", "date": "2026-05-10", "net_price": 1.42},
    ])
    assert n == 1
    # the write landed in benchmark.db ...
    assert _count(pi._bench, "my_prices") == 1
    # ... and the engine-owned product DB has no my_prices table written by the app.
    assert _table_exists(pi._fuel, "transactions")        # product DB is intact
    assert _count(pi._fuel, "my_prices") == 0


def test_margin_reader_joins_product_and_benchmark(pi):
    """The margin/vs_my_price reader joins transactions (product, read-only) with
    my_prices (benchmark) and returns rows with a benchmark-derived gap."""
    _make_transactions(pi._fuel)
    pi.load_my_prices([
        {"country": "Belgium", "city": "Antwerp", "date": "2026-05-15", "net_price": 1.40},
    ])
    rows, summary = pi.margin_report(None, "month")
    be = next(r for r in rows if r["country"] == "Belgium")
    assert be["eff_price"] == 1.50            # 1500 / 1000, from product transactions
    assert be["my_price"] == 1.40             # from benchmark my_prices
    assert be["gap_vs_my"] == 0.10            # supplier NET above your benchmark
    assert summary["rows"] == 1


def test_one_time_copy_is_idempotent(pi, monkeypatch):
    """Legacy my_prices/wholesale_prices still in fuel_history.db are copied ONCE into
    benchmark.db; running connect() again does not duplicate them."""
    # Seed legacy benchmark rows in the (engine-owned) product DB, as a pre-D3 system
    # would have had them.
    prod = sqlite3.connect(pi._fuel)
    prod.execute("""CREATE TABLE my_prices (
        country TEXT, city TEXT, date TEXT, product_group TEXT DEFAULT 'Diesel',
        net_price REAL, source TEXT DEFAULT 'upload',
        PRIMARY KEY (country, city, date, product_group))""")
    prod.execute("""CREATE TABLE wholesale_prices (
        country TEXT, date TEXT, product_group TEXT DEFAULT 'Diesel',
        net_price REAL, source TEXT,
        PRIMARY KEY (country, date, product_group))""")
    prod.executemany(
        "INSERT INTO my_prices (country,city,date,product_group,net_price,source)"
        " VALUES (?,?,?,?,?,?)", [
            ("Belgium", "Antwerp", "2026-05-10", "Diesel", 1.41, "upload"),
            ("Spain", "Madrid", "2026-05-11", "Diesel", 1.39, "upload"),
        ])
    prod.execute("INSERT INTO wholesale_prices (country,date,product_group,net_price,source)"
                 " VALUES ('Belgium','2026-05-10','Diesel',1.20,'index')")
    prod.commit(); prod.close()

    # First connect() triggers the one-time copy.
    pi._MIGRATED.clear()
    con = pi.connect()
    assert con.execute("SELECT COUNT(*) FROM my_prices").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM wholesale_prices").fetchone()[0] == 1
    con.close()

    # Run again (clear the process guard to force the empty-check path) — still 2/1,
    # no duplicates.
    pi._MIGRATED.clear()
    con = pi.connect()
    assert con.execute("SELECT COUNT(*) FROM my_prices").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM wholesale_prices").fetchone()[0] == 1
    con.close()

    # The originals are LEFT in place in the product DB (same precedent as the
    # analytics migration — the engine-owned DB just stops being written by the app).
    assert _count(pi._fuel, "my_prices") == 2
