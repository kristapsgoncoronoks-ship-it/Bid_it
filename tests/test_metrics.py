"""
MATERIALIZED AGGREGATES (metrics.py): the per-period dashboard totals are settled at
the monthly close into the engine-owned `settled_metrics` table, recomputed via the
CANONICAL queries.py functions (NOT a forked math), with a recompute-and-compare drift
check.

These tests stand up a temp fuel_history.db the same way the other engine tests do
(period-stamped consolidate pickle -> history.load), then exercise:
  - rebuild() writes settled_metrics whose values EQUAL a fresh queries.q_savings /
    q_kpis call on the same data (proves not-forked);
  - rebuild() is idempotent (REPLACE on (period, metric), no duplication);
  - read() on a DB with no settled_metrics table returns {} (pre-migration tolerance);
  - verify() returns [] when settled matches live, and reports the right
    stored/live/delta after a stored value is mutated to a wrong number;
  - the app's READ-ONLY dataproduct handle cannot write settled_metrics (boundary holds);
  - the dashboard renders 200 from settled metrics when present, and still renders via
    the live fallback when the table is absent.
"""
import os
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import consolidate  # noqa: E402
import dataproduct  # noqa: E402
import history  # noqa: E402
import metrics  # noqa: E402
import queries  # noqa: E402

PERIOD = "2099-09"


# FIELDS order: entity,supplier,country,vehicle,date,time,station,product,
# product_group,qty,currency,net_local,vat_local,gross_local,net_eur,vat_eur,
# net_eur_eff,note
def _rows():
    """Two same-day, same-country diesel suppliers (so the overpay loop has a premium to
    attribute) plus a third country/day, so q_savings/q_kpis produce non-trivial totals."""
    return [
        # BE 2099-09-01: TFC cheaper, Q8 dearer -> Q8 overpays vs TFC
        ["ENT", "TFC", "BE", "C1", "2099-09-01", "08:00", "St BE 1", "Diesel",
         "Diesel", 500.0, "EUR", 700.0, 147.0, 847.0, 700.0, 147.0, 690.0, ""],
        ["ENT", "Q8", "BE", "C2", "2099-09-01", "09:00", "St BE 2", "Diesel",
         "Diesel", 400.0, "EUR", 600.0, 126.0, 726.0, 600.0, 126.0, 600.0, ""],
        # LT 2099-09-02: single supplier (no rival) -> no overpay, still counts to KPIs
        ["ENT", "BP", "LT", "C3", "2099-09-02", "10:00", "St LT", "Diesel",
         "Diesel", 300.0, "EUR", 420.0, 88.2, 508.2, 420.0, 88.2, 420.0, ""],
        # a non-diesel line so diesel litres != total litres
        ["ENT", "Q8", "BE", "C2", "2099-09-01", "09:30", "St BE 2", "AdBlue",
         "Other", 50.0, "EUR", 40.0, 8.4, 48.4, 40.0, 8.4, 40.0, ""],
    ]


@pytest.fixture()
def fh_db(tmp_path, monkeypatch):
    """Write a period-stamped pickle of _rows(), point history.DB + metrics.DB + the app's
    read-only dataproduct path at one temp fuel_history.db, and run history.load(PERIOD).
    Yield the temp DB path (transactions loaded, settled_metrics NOT yet built)."""
    pkl = str(tmp_path / "consolidated_rows.pkl")
    consolidate._dump_pickle(_rows(), PERIOD, path=pkl)
    real = consolidate.load_rows
    monkeypatch.setattr(consolidate, "load_rows",
                        lambda period, path=pkl: real(period, path=path))

    db = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(history, "DB", db, raising=True)
    monkeypatch.setattr(metrics, "DB", db, raising=True)
    # repoint the app's read-only window so read()/verify()/the dashboard see this DB
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", db)

    history.load(PERIOD)
    return db


def _writable(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# rebuild() values equal the canonical queries (proves not-forked)
# ---------------------------------------------------------------------------
def test_rebuild_matches_canonical_queries(fh_db):
    metrics.rebuild(PERIOD)               # engine writer path (opens its own writable con)

    # fresh canonical recompute on the same data
    con = _writable(fh_db)
    try:
        sv = queries.q_savings(con, PERIOD)
        k = queries.q_kpis(con, PERIOD)
    finally:
        con.close()

    got = metrics.read(PERIOD)            # read-only window
    assert set(got) >= {metrics.M_OVERPAY, metrics.M_LITRES, metrics.M_EURL,
                        metrics.M_NET, metrics.M_VAT}

    import money
    assert got[metrics.M_OVERPAY]["value"] == money.f2(sv["total"])
    assert got[metrics.M_NET]["value"] == money.f2(k["net"])
    assert got[metrics.M_VAT]["value"] == money.f2(k["vat"])
    assert got[metrics.M_LITRES]["value"] == k["litres"]
    assert abs(got[metrics.M_EURL]["value"] - k["eurl"]) < 1e-9
    # the overpay is real (Q8 dearer than TFC on 2099-09-01) and carries its breakdown
    assert got[metrics.M_OVERPAY]["value"] > 0
    detail = got[metrics.M_OVERPAY]["detail"]
    assert detail and "by_country" in detail and "by_supplier" in detail


def test_rebuild_idempotent_no_duplication(fh_db):
    metrics.rebuild(PERIOD)
    metrics.rebuild(PERIOD)              # second settle must REPLACE, not duplicate
    con = _writable(fh_db)
    try:
        n = con.execute("SELECT COUNT(*) FROM settled_metrics WHERE period=?",
                        (PERIOD,)).fetchone()[0]
        dups = con.execute(
            "SELECT metric, COUNT(*) c FROM settled_metrics WHERE period=? "
            "GROUP BY metric HAVING c>1", (PERIOD,)).fetchall()
    finally:
        con.close()
    assert dups == [], f"duplicate metric rows: {dups}"
    assert n == 5, n
    assert metrics.rebuild(PERIOD) == {"period": PERIOD, "metrics": 5}


# ---------------------------------------------------------------------------
# read() tolerates a pre-migration DB (no settled_metrics table)
# ---------------------------------------------------------------------------
def test_read_missing_table_returns_empty(fh_db):
    # history.load built transactions but NOT settled_metrics (rebuild not yet run)
    con = _writable(fh_db)
    try:
        tbls = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert "settled_metrics" not in tbls
    assert metrics.read(PERIOD) == {}        # no crash, empty dict


# ---------------------------------------------------------------------------
# verify() drift check
# ---------------------------------------------------------------------------
def test_verify_clean_then_drift(fh_db):
    metrics.rebuild(PERIOD)
    assert metrics.verify(PERIOD) == []      # settled matches a live recompute

    # mutate a STORED metric to a wrong value and confirm verify catches exactly it
    con = _writable(fh_db)
    try:
        live_net = queries.q_kpis(con, PERIOD)["net"]
        con.execute("UPDATE settled_metrics SET value=? WHERE period=? AND metric=?",
                    (live_net + 1000.0, PERIOD, metrics.M_NET))
        con.commit()
    finally:
        con.close()

    drifts = metrics.verify(PERIOD)
    assert len(drifts) == 1, drifts
    d = drifts[0]
    assert d["metric"] == metrics.M_NET
    assert abs(d["stored"] - (live_net + 1000.0)) < 1e-9
    assert abs(d["live"] - live_net) < 1e-9
    assert abs(d["delta"] - (live_net - (live_net + 1000.0))) < 1e-9   # live - stored


def test_verify_empty_when_nothing_settled(fh_db):
    # no rebuild -> nothing to drift against -> []
    assert metrics.verify(PERIOD) == []


# ---------------------------------------------------------------------------
# read-only boundary: the app's dataproduct handle cannot write settled_metrics
# ---------------------------------------------------------------------------
def test_app_handle_cannot_write_settled_metrics(fh_db):
    metrics.rebuild(PERIOD)               # ensure the table exists
    ro = dataproduct.connect("fuel_history")
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("UPDATE settled_metrics SET value=999 WHERE period=?", (PERIOD,))
            ro.commit()
    finally:
        ro.close()


# ---------------------------------------------------------------------------
# web: dashboard renders 200 from settled metrics, and via the live fallback
# ---------------------------------------------------------------------------
def test_dashboard_renders_with_settled_metrics(fh_db, client):
    metrics.rebuild(PERIOD)
    r = client.get(f"/?period={PERIOD}")
    assert r.status_code == 200, r.status_code
    body = r.get_data(as_text=True)
    assert "Diesel litres" in body
    assert "Avoidable overpay" in body


def test_dashboard_renders_live_fallback_without_settled(fh_db, client):
    # no rebuild: settled_metrics table absent -> dashboard must fall back to live
    r = client.get(f"/?period={PERIOD}")
    assert r.status_code == 200, r.status_code
    body = r.get_data(as_text=True)
    assert "Diesel litres" in body
