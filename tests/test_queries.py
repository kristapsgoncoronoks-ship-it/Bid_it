"""Tests for the extracted query brick (queries.py) against the shipped data."""
import sqlite3
import os

from werkzeug.datastructures import MultiDict

import queries as Q

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fuel_history.db")


def _con():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    return c


def test_periods_and_filters():
    con = _con()
    assert "2026-05" in Q.q_periods(con)
    f = Q.q_filters(con)
    assert set(["Q8", "BP", "DKV"]).issubset(set(f["suppliers"]))
    assert "periods" in f and "stations" in f
    con.close()


def test_where_multi_value():
    w, p = Q.where(MultiDict([("supplier", "Q8"), ("supplier", "BP")]), period="2026-05")
    assert "supplier IN (?,?)" in w
    assert "period=?" in w
    assert "Q8" in p and "BP" in p and "2026-05" in p


def test_compare_filtered_to_suppliers():
    con = _con()
    rows = Q.q_compare(con, MultiDict([("supplier", "Q8"), ("supplier", "BP")]), period="2026-05")
    assert rows and {r["supplier"] for r in rows} <= {"Q8", "BP"}
    con.close()


def test_kpis_and_savings():
    con = _con()
    k = Q.q_kpis(con, "2026-05")
    assert k["net"] and k["net"] > 0
    sv = Q.q_savings(con, "2026-05")
    assert sv["total"] >= 0 and isinstance(sv["by_supplier"], list)
    con.close()
