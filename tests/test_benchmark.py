"""Self-sourced competitor benchmark: best-of price + avoidable overpay derived only
from your own multi-supplier purchases (no external data)."""
import importlib

import pytest


@pytest.fixture()
def pi(tmp_path, monkeypatch):
    import sqlite3
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    fuel = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(pricing_intelligence, "DB", fuel)
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))
    # transactions live in the engine-owned product DB (read read-only by the app).
    prod = sqlite3.connect(fuel)
    prod.execute("""CREATE TABLE IF NOT EXISTS transactions (
        period TEXT, country TEXT, supplier TEXT, date TEXT, product_group TEXT,
        qty REAL, net_eur_eff REAL)""")
    # Belgium 2026-05: BP cheaper than TFC; both 1000 L
    prod.executemany("INSERT INTO transactions (period,country,supplier,date,product_group,qty,net_eur_eff)"
                    " VALUES (?,?,?,?,?,?,?)", [
        ("2026-05", "Belgium", "BP",  "2026-05-10", "Diesel", 1000, 1400.0),   # 1.40 €/L
        ("2026-05", "Belgium", "TFC", "2026-05-12", "Diesel", 1000, 1500.0),   # 1.50 €/L
        ("2026-05", "Spain",   "MOEVE","2026-05-09", "Diesel", 500, 650.0),    # single supplier
    ])
    prod.commit(); prod.close()
    return pricing_intelligence


def test_benchmark_best_and_overpay(pi):
    rows, summ = pi.internal_benchmark(None, "month")
    be = next(r for r in rows if r["country"] == "Belgium")
    assert be["suppliers"] == 2
    assert be["best_supplier"] == "BP" and be["best_price"] == 1.40
    # overpay = TFC volume * (1.50 - 1.40) = 1000 * 0.10 = 100
    assert be["overpay_eur"] == 100.0
    assert summ["multi_supplier_cells"] == 1            # only Belgium has 2+
    assert summ["total_overpay"] == 100.0


def test_single_supplier_cell_has_no_overpay(pi):
    rows, _ = pi.internal_benchmark(None, "month")
    es = next(r for r in rows if r["country"] == "Spain")
    assert es["suppliers"] == 1 and es["overpay_eur"] == 0.0


def test_adopt_loads_my_prices_internal(pi):
    n = pi.adopt_internal_benchmark(None, "month")
    assert n == 2                                       # Belgium + Spain best prices
    con = pi.connect()
    rows = {r["country"]: r for r in con.execute(
        "SELECT country, net_price, source FROM my_prices")}
    con.close()
    assert rows["Belgium"]["net_price"] == 1.40 and rows["Belgium"]["source"] == "internal"


def test_workbook_exports(pi, tmp_path):
    import os
    p = pi.internal_benchmark_workbook(None, "month", path=str(tmp_path / "bench.xlsx"))
    assert os.path.exists(p) and open(p, "rb").read(2) == b"PK"
