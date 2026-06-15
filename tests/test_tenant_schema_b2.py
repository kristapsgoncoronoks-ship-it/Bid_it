"""
MULTI-TENANT P1 (schema plumbing) — fuel_history.db, the ENGINE-owned product DB.

This is the most delicate P1 slice: fuel_history.db is written ONLY by the engine
(history.load / metrics.rebuild / invoice_control.run_control); the app reads it
READ-ONLY via dataproduct.connect("fuel_history") and holds NO writable handle. So
the tenant_id column is added on each module's OWN writable engine path:

  - transactions          (history.py _MIGR, applied in load())
  - settled_metrics       (metrics.py _MIGR, applied in rebuild())
  - invoice_receipt_control (invoice_control._control_writer, migration key
                             "invoice_control_fuel", applied in run_control(persist=True))

PURE PLUMBING, ZERO behavior change: the column is TEXT NOT NULL DEFAULT 'default'
(tenancy.tenant_column_ddls); the engine INSERTs are all explicit-column, so rows
take the DEFAULT. NOTHING SELECTs/filters the column (scope_clause is wired in P2),
multitenant is OFF, and the read paths (metrics.read / queries) stay byte-identical.

These tests stand up a temp fuel_history.db exactly the way test_history.py /
test_metrics.py / test_invoice_control.py do (period-stamped pickle -> history.load,
and a hermetic transactions table for the control writer), drive the ENGINE writer
paths, and assert: the column lands with the right DEFAULT, loaded rows stamp
'default', the app READ path is unaffected, the read-only boundary still raises, and
a second run is idempotent (no re-ALTER, no error).
"""
import os
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import consolidate      # noqa: E402
import dataproduct      # noqa: E402
import history          # noqa: E402
import invoice_control  # noqa: E402
import metrics          # noqa: E402
import queries          # noqa: E402
import tenancy          # noqa: E402

PERIOD = "2099-09"


# FIELDS order: entity,supplier,country,vehicle,date,time,station,product,
# product_group,qty,currency,net_local,vat_local,gross_local,net_eur,vat_eur,
# net_eur_eff,note
def _rows():
    return [
        ["ENT", "TFC", "BE", "C1", "2099-09-01", "08:00", "St BE 1", "Diesel",
         "Diesel", 500.0, "EUR", 700.0, 147.0, 847.0, 700.0, 147.0, 690.0, ""],
        ["ENT", "Q8", "BE", "C2", "2099-09-01", "09:00", "St BE 2", "Diesel",
         "Diesel", 400.0, "EUR", 600.0, 126.0, 726.0, 600.0, 126.0, 600.0, ""],
        ["ENT", "BP", "LT", "C3", "2099-09-02", "10:00", "St LT", "Diesel",
         "Diesel", 300.0, "EUR", 420.0, 88.2, 508.2, 420.0, 88.2, 420.0, ""],
    ]


def _col(con, table, name):
    """Return the PRAGMA table_info row (as a dict) for `name`, or None."""
    for r in con.execute(f"PRAGMA table_info({table})"):
        # (cid, name, type, notnull, dflt_value, pk)
        if r[1] == name:
            return {"type": r[2], "notnull": r[3], "default": r[4]}
    return None


def _assert_tenant_column(con, table):
    """The tenant column lands as TEXT NOT NULL DEFAULT 'default' on `table`."""
    c = _col(con, table, tenancy.TENANT_COLUMN)
    assert c is not None, f"{table}.{tenancy.TENANT_COLUMN} missing"
    assert c["type"].upper() == "TEXT", c
    assert c["notnull"] == 1, c
    # default is stored quoted in PRAGMA; both 'default' and "'default'" guard
    assert tenancy.DEFAULT_TENANT_ID in (c["default"] or ""), c


def _writable(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# transactions (history.load)
# ---------------------------------------------------------------------------
@pytest.fixture()
def fh_db(tmp_path, monkeypatch):
    """Point history.DB / metrics.DB / the read-only dataproduct path at one temp
    fuel_history.db and run history.load(PERIOD) (transactions loaded; settled_metrics
    NOT yet built)."""
    pkl = str(tmp_path / "consolidated_rows.pkl")
    consolidate._dump_pickle(_rows(), PERIOD, path=pkl)
    real = consolidate.load_rows
    monkeypatch.setattr(consolidate, "load_rows",
                        lambda period, path=pkl: real(period, path=path))

    db = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(history, "DB", db, raising=True)
    monkeypatch.setattr(metrics, "DB", db, raising=True)
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", db)

    history.load(PERIOD)
    return db


def test_transactions_has_tenant_column_default(fh_db):
    """After history.load, transactions carries tenant_id TEXT NOT NULL DEFAULT
    'default' and every loaded row is stamped 'default' (the explicit-column INSERT
    leaves tenant_id to its DEFAULT)."""
    con = _writable(fh_db)
    try:
        _assert_tenant_column(con, "transactions")
        rows = con.execute(
            "SELECT tenant_id FROM transactions WHERE period=?", (PERIOD,)).fetchall()
        assert rows, "no transactions loaded"
        assert all(r["tenant_id"] == tenancy.DEFAULT_TENANT_ID for r in rows), \
            [dict(r) for r in rows]
    finally:
        con.close()


def test_history_load_idempotent_no_realter(fh_db):
    """A second history.load re-runs the DELETE+INSERT but must NOT re-run the ALTER
    (db_migrate is versioned) — no error, column still present, rows still 'default'."""
    history.load(PERIOD)   # second close of the same period
    con = _writable(fh_db)
    try:
        _assert_tenant_column(con, "transactions")
        assert con.execute(
            "SELECT COUNT(*) FROM transactions WHERE period=? AND tenant_id<>?",
            (PERIOD, tenancy.DEFAULT_TENANT_ID)).fetchone()[0] == 0
    finally:
        con.close()


def test_transactions_reads_unaffected(fh_db):
    """The canonical read path (queries.q_kpis) returns the same figures — tenant_id is
    not surfaced and not filtered (P1 = zero behavior change)."""
    con = _writable(fh_db)
    try:
        k = queries.q_kpis(con, PERIOD)
    finally:
        con.close()
    # from _rows(): 1200 L diesel, net 700+600+420=1720.0, VAT 147+126+88.2=361.2.
    # q_kpis ROUNDs net/vat to whole EUR (ROUND(...,0)) -> net 1720.0, vat 361.0.
    assert k["litres"] == 1200.0
    assert abs(k["net"] - 1720.0) < 1e-9
    assert abs(k["vat"] - 361.0) < 1e-9


# ---------------------------------------------------------------------------
# settled_metrics (metrics.rebuild)
# ---------------------------------------------------------------------------
def test_settled_metrics_has_tenant_column_default(fh_db):
    """After metrics.rebuild, settled_metrics carries tenant_id defaulting 'default'
    and every settled row is stamped 'default' (explicit INSERT)."""
    metrics.rebuild(PERIOD)
    con = _writable(fh_db)
    try:
        _assert_tenant_column(con, "settled_metrics")
        rows = con.execute(
            "SELECT tenant_id FROM settled_metrics WHERE period=?", (PERIOD,)).fetchall()
        assert rows
        assert all(r["tenant_id"] == tenancy.DEFAULT_TENANT_ID for r in rows)
    finally:
        con.close()


def test_metrics_rebuild_idempotent_and_read_unaffected(fh_db):
    """A second rebuild does not re-ALTER or error, and metrics.read returns identical
    values (tenant_id not surfaced)."""
    metrics.rebuild(PERIOD)
    first = metrics.read(PERIOD)
    metrics.rebuild(PERIOD)            # idempotent (no re-ALTER, REPLACE rows)
    second = metrics.read(PERIOD)
    assert first == second
    # tenant_id is NOT surfaced by the read API
    for m, v in second.items():
        assert set(v) == {"value", "detail"}, (m, v)


def test_app_handle_cannot_write_settled_metrics(fh_db):
    """The read-only boundary holds: a stray app write to settled_metrics still raises
    OperationalError even with the new column present."""
    metrics.rebuild(PERIOD)
    ro = dataproduct.connect("fuel_history")
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("UPDATE settled_metrics SET value=999 WHERE period=?", (PERIOD,))
            ro.commit()
    finally:
        ro.close()


# ---------------------------------------------------------------------------
# invoice_receipt_control (invoice_control.run_control, persist=True)
# ---------------------------------------------------------------------------
@pytest.fixture()
def control_db(tmp_path, monkeypatch):
    """Hermetic period: a temp fuel_history.db with a transactions table + one
    VAT-bearing line, a temp suppliers.db with one cadence supplier, and a temp
    vat_claims.db vault. Mirrors test_invoice_control.tinydb so run_control(persist=True)
    creates + migrates + writes invoice_receipt_control on the engine writer path."""
    import supplier_master, vat_refund

    fh = str(tmp_path / "fuel_history.db")
    con = sqlite3.connect(fh)
    con.execute("""CREATE TABLE transactions (
        period TEXT, supplier TEXT, country TEXT, date TEXT, qty REAL)""")
    con.execute("INSERT INTO transactions (period, supplier, country, date, qty) "
                "VALUES (?,?,?,?,?)", (PERIOD, "BP", "Germany", f"{PERIOD}-10", 100.0))
    con.commit(); con.close()

    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "fh_legacy.db"))
    vat_refund._SCHEMA_READY.clear()
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setattr(invoice_control, "FUEL_HISTORY_DB", fh)
    vat_refund.connect().close()

    scon = supplier_master.connect()
    scon.execute("INSERT INTO suppliers (code, invoice_cadence) VALUES (?,?)",
                 ("BP", "monthly"))
    scon.commit(); scon.close()
    return fh


def test_invoice_receipt_control_has_tenant_column_default(control_db):
    """After run_control(persist=True), invoice_receipt_control carries tenant_id
    defaulting 'default' and persisted rows are stamped 'default' (the explicit-column
    INSERT leaves tenant_id to its DEFAULT)."""
    rows, _ = invoice_control.run_control(PERIOD, persist=True)
    assert rows
    con = _writable(control_db)
    try:
        _assert_tenant_column(con, "invoice_receipt_control")
        stamped = con.execute(
            "SELECT tenant_id FROM invoice_receipt_control WHERE period=?",
            (PERIOD,)).fetchall()
        assert stamped
        assert all(r["tenant_id"] == tenancy.DEFAULT_TENANT_ID for r in stamped)
    finally:
        con.close()


def test_invoice_receipt_control_idempotent_no_realter(control_db):
    """A second run_control does not re-run the migration ALTER or error; the column
    stays and rows stay 'default'. The read render path (control_summary) is unaffected
    and returns the same rows."""
    rw_rows, _ = invoice_control.run_control(PERIOD, persist=True)
    ro_rows, _ = invoice_control.control_summary(PERIOD)
    assert ro_rows == rw_rows
    invoice_control.run_control(PERIOD, persist=True)   # second close: no re-ALTER
    con = _writable(control_db)
    try:
        _assert_tenant_column(con, "invoice_receipt_control")
        assert con.execute(
            "SELECT COUNT(*) FROM invoice_receipt_control "
            "WHERE period=? AND tenant_id<>?",
            (PERIOD, tenancy.DEFAULT_TENANT_ID)).fetchone()[0] == 0
    finally:
        con.close()
