"""De-dup verification: reports._savings now delegates to the single canonical
avoidable-overpay loop in queries.q_savings. Asserts the two agree (byte-identical
report/page output) and covers the hand-verifiable overpay number plus the
<2-suppliers and non-positive-overpay edges. NET EUR/L basis.

Note: `total` is quantized once via money.f2 (HALF_UP); `by_supplier`/`by_country`
are deliberately left at full precision (charted at integer-EUR display), so they
carry IEEE-754 drift — assertions on them round per item."""
import sqlite3

import queries
import reports


def _round_items(items):
    return {k: round(v, 6) for k, v in items}


def _seed(rows):
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE transactions (period TEXT, date TEXT, country TEXT,
                   supplier TEXT, product_group TEXT, qty REAL, net_eur_eff REAL)""")
    con.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    return con


def test_hand_verifiable_overpay():
    # A 100L@1.40, B 100L@1.50, same date+country -> B overpays 100*(1.50-1.40)=10.00,
    # attributed to B and to that country.
    con = _seed([
        ("2026-05", "2026-05-10", "Latvia", "A", "Diesel", 100.0, 140.0),
        ("2026-05", "2026-05-10", "Latvia", "B", "Diesel", 100.0, 150.0),
    ])
    sv = queries.q_savings(con, "2026-05")
    con.close()
    assert sv["total"] == 10.0
    assert _round_items(sv["by_supplier"]) == {"B": 10.0}
    assert _round_items(sv["by_country"]) == {"Latvia": 10.0}


def test_savings_matches_canonical():
    # reports._savings must equal queries.q_savings with the remapped key names.
    con = _seed([
        ("2026-05", "2026-05-10", "Latvia", "A", "Diesel", 100.0, 140.0),
        ("2026-05", "2026-05-10", "Latvia", "B", "Diesel", 100.0, 150.0),
        ("2026-05", "2026-05-11", "Estonia", "A", "Diesel", 50.0, 60.0),
        ("2026-05", "2026-05-11", "Estonia", "C", "Diesel", 50.0, 75.0),
    ])
    q = queries.q_savings(con, "2026-05")
    r = reports._savings(con, "2026-05")
    con.close()
    assert r["total"] == q["total"]
    assert r["by_sup"] == q["by_supplier"]
    assert r["by_ctry"] == q["by_country"]


def test_single_supplier_no_overpay():
    # A day+country with only ONE supplier has no rival -> no overpay.
    con = _seed([
        ("2026-05", "2026-05-10", "Latvia", "A", "Diesel", 100.0, 140.0),
    ])
    sv = queries.q_savings(con, "2026-05")
    con.close()
    assert sv["total"] == 0.0
    assert sv["by_supplier"] == []
    assert sv["by_country"] == []


def test_cheapest_supplier_not_penalised():
    # The cheapest supplier's overpay is 0 (skipped); only the premium one is charged.
    con = _seed([
        ("2026-05", "2026-05-10", "Latvia", "CHEAP", "Diesel", 100.0, 130.0),
        ("2026-05", "2026-05-10", "Latvia", "MID", "Diesel", 100.0, 140.0),
        ("2026-05", "2026-05-10", "Latvia", "DEAR", "Diesel", 100.0, 150.0),
    ])
    sv = queries.q_savings(con, "2026-05")
    con.close()
    # CHEAP=0 (skipped), MID=100*(1.40-1.30)=10, DEAR=100*(1.50-1.30)=20.
    assert sv["total"] == 30.0
    assert _round_items(sv["by_supplier"]) == {"DEAR": 20.0, "MID": 10.0}
    assert "CHEAP" not in dict(sv["by_supplier"])
