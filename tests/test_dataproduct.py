"""
The data-processing engine OWNS the product DBs (fuel_history.db); the app reads
them READ-ONLY through the single `dataproduct` accessor. These tests assert the
boundary holds:
  (1) a write through dataproduct.connect("fuel_history") raises (read-only handle);
  (2) a queries.py read through dataproduct returns the same rows as a direct read.
SQLite-only (the read-only `mode=ro` URI is a SQLite mechanism; Postgres uses a
read-only role — see dataproduct._role()).
"""
import os, sys, sqlite3
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import dataproduct
import queries

pytestmark = pytest.mark.skipif(db.ENGINE != "sqlite",
                                reason="read-only URI handle is a SQLite mechanism")


def _has_transactions():
    if not os.path.exists(dataproduct._PATHS["fuel_history"]):
        return False
    con = dataproduct.connect("fuel_history")
    try:
        con.execute("SELECT 1 FROM transactions LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def test_unknown_product_rejected():
    with pytest.raises(ValueError):
        dataproduct.connect("not_a_product")


def test_write_through_dataproduct_raises():
    """A read-only handle must refuse any write — this is the boundary guard that
    surfaces a stray app-side write instead of letting it mutate the engine-owned DB."""
    if not _has_transactions():
        pytest.skip("fuel_history.db / transactions not present (run the engine first)")
    con = dataproduct.connect("fuel_history")
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO transactions (period) VALUES ('ZZZZ-99')")
            con.commit()
    finally:
        con.close()


def test_suppliers_window_is_read_only():
    """The `suppliers` read-window exposed via dataproduct is ALSO read-only: the app
    reads supplier master through it and must never mutate it via this handle (the admin
    CRM has its own writable supplier_master.connect path; this accessor is read-only)."""
    if not os.path.exists(dataproduct._PATHS["suppliers"]):
        pytest.skip("suppliers.db not present")
    con = dataproduct.connect("suppliers")
    try:
        # read works
        con.execute("SELECT 1 FROM suppliers LIMIT 1")
        # any write must raise — the boundary guard
        with pytest.raises(sqlite3.OperationalError):
            con.execute("UPDATE suppliers SET legal_name = legal_name")
            con.commit()
    finally:
        con.close()


def test_read_matches_direct():
    """queries.py reads through the read-only handle return the same rows as a plain
    direct read of the same file."""
    if not _has_transactions():
        pytest.skip("fuel_history.db / transactions not present (run the engine first)")
    ro = dataproduct.connect("fuel_history")
    direct = sqlite3.connect(dataproduct._PATHS["fuel_history"])
    direct.row_factory = sqlite3.Row
    try:
        assert queries.q_periods(ro) == queries.q_periods(direct)
        ro_n = ro.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        direct_n = direct.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert ro_n == direct_n
    finally:
        ro.close()
        direct.close()
