"""
FX provenance (../README.md#data-architecture-duplication-under-used-data finding #4): the engine persists the APPLIED
local->EUR rate per transaction line in transactions.fx_rate at consolidation time,
so a historical claim's EUR is traceable to a stored rate even if FX sources change.

These tests run history.load against a temp fuel_history.db fed by a period-stamped
consolidate pickle (the same isolation pattern test_engine_close uses), and assert:
  - fx_rate is populated and equals net_local/net_eur (foreign units per 1 EUR) for a
    non-EUR line, and ~1.0 for an EUR-native line;
  - a row with net_eur == 0 / NULL stores fx_rate NULL (no div-by-zero, no crash);
  - the additive migration is idempotent (db_migrate versioning) and DOES NOT touch the
    existing net_*/vat_* figures.
"""
import os
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import consolidate  # noqa: E402
import ecb_rates  # noqa: E402
import history  # noqa: E402

PERIOD = "2099-07"


# FIELDS order: entity,supplier,country,vehicle,date,time,station,product,
# product_group,qty,currency,net_local,vat_local,gross_local,net_eur,vat_eur,
# net_eur_eff,note
def _rows():
    return [
        # non-EUR (BP/PLN-like): 4.27 PLN per EUR applied -> net_eur = net_local/4.27
        ["ENT", "BP", "PL", "CARP", "2026-05-10", "08:00", "Stat PL", "Diesel",
         "Diesel", 500.0, "PLN", 4270.0, 982.10, 5252.10, 1000.0, 230.0, 1000.0, ""],
        # EUR-native: net_local == net_eur -> rate 1.0
        ["ENT", "TFC", "BE", "CARE", "2026-05-11", "09:00", "Stat BE", "Diesel",
         "Diesel", 300.0, "EUR", 450.0, 94.50, 544.50, 450.0, 94.50, 445.0, ""],
        # no EUR basis: net_eur == 0 -> fx_rate must be NULL (no div-by-zero)
        ["ENT", "X", "LT", "CARN", "2026-05-12", "10:00", "Stat LT", "Pump test",
         "Other", 0.0, "EUR", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "zero line"],
    ]


@pytest.fixture()
def loaded(tmp_path, monkeypatch):
    """Write a period-stamped pickle of _rows(), point history.DB at a temp file, and
    run history.load(PERIOD). Yield the temp DB path."""
    pkl = str(tmp_path / "consolidated_rows.pkl")
    consolidate._dump_pickle(_rows(), PERIOD, path=pkl)

    real = consolidate.load_rows
    monkeypatch.setattr(consolidate, "load_rows",
                        lambda period, path=pkl: real(period, path=path))
    hist_db = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(history, "DB", hist_db, raising=True)

    history.load(PERIOD)
    return hist_db


def _fetch(db, supplier):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT net_local, net_eur, vat_eur, fx_rate FROM transactions "
            "WHERE period=? AND supplier=?", (PERIOD, supplier)).fetchone()
    finally:
        con.close()


def test_fx_rate_column_exists(loaded):
    con = sqlite3.connect(loaded)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(transactions)")}
    finally:
        con.close()
    assert "fx_rate" in cols, cols


def test_non_eur_line_stores_applied_rate(loaded):
    net_local, net_eur, _vat, fx = _fetch(loaded, "BP")
    assert fx is not None
    assert abs(fx - net_local / net_eur) < 1e-9, (fx, net_local, net_eur)
    # the applied rate is the one that produced net_eur: net_local / fx ~= net_eur
    assert abs(net_local / fx - net_eur) < 1e-6


def test_eur_native_line_rate_is_one(loaded):
    net_local, net_eur, _vat, fx = _fetch(loaded, "TFC")
    assert net_local == net_eur
    assert fx is not None and abs(fx - 1.0) < 1e-12, fx


def test_zero_eur_basis_stores_null(loaded):
    net_local, net_eur, _vat, fx = _fetch(loaded, "X")
    assert net_eur == 0.0
    assert fx is None, f"expected NULL fx_rate for zero-EUR line, got {fx!r}"


def test_fx_rate_helper_null_guards():
    # explicit unit coverage of the populate logic's guards
    assert history.fx_rate(None, 100.0) is None       # no local amount
    assert history.fx_rate(100.0, 0.0) is None        # zero EUR basis
    assert history.fx_rate(100.0, None) is None        # NULL EUR basis
    assert abs(history.fx_rate(427.0, 100.0) - 4.27) < 1e-12
    assert history.fx_rate(450.0, 450.0) == 1.0        # EUR-native


# ---------------------------------------------------------------------------
# Official ECB reference per line (owner-directed independent verification):
# fx_ecb_rate / fx_ecb_date / fx_source frozen on each line at consolidation.
# ---------------------------------------------------------------------------
@pytest.fixture()
def loaded_with_ecb(tmp_path, monkeypatch):
    """As `loaded`, but with a SEEDED ecb_rates.db so the non-EUR line gets a covered
    'ecb' reference. Seeds PLN 4.27 on/before the BP line's date (2026-05-10)."""
    ecb_db = str(tmp_path / "ecb_rates.db")
    monkeypatch.setattr(ecb_rates, "DB", ecb_db, raising=True)
    ecb_rates.store([("2026-05-09", "PLN", 4.27)], source="test seed")

    pkl = str(tmp_path / "consolidated_rows.pkl")
    consolidate._dump_pickle(_rows(), PERIOD, path=pkl)
    real = consolidate.load_rows
    monkeypatch.setattr(consolidate, "load_rows",
                        lambda period, path=pkl: real(period, path=path))
    hist_db = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(history, "DB", hist_db, raising=True)
    history.load(PERIOD)
    return hist_db


def _fetch_ecb(db, supplier):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT net_eur, fx_rate, fx_ecb_rate, fx_ecb_date, fx_source "
            "FROM transactions WHERE period=? AND supplier=?", (PERIOD, supplier)).fetchone()
    finally:
        con.close()


def test_ecb_reference_columns_exist(loaded_with_ecb):
    con = sqlite3.connect(loaded_with_ecb)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(transactions)")}
    finally:
        con.close()
    assert {"fx_ecb_rate", "fx_ecb_date", "fx_source"} <= cols, cols


def test_covered_non_eur_line_stores_ecb_reference(loaded_with_ecb):
    net_eur, fx, ecb_rate, ecb_date, src = _fetch_ecb(loaded_with_ecb, "BP")
    assert src == "ecb"
    assert abs(ecb_rate - 4.27) < 1e-12, ecb_rate
    assert ecb_date == "2026-05-09"           # rate on/before the line's 2026-05-10 date
    # figures are UNCHANGED — net_eur still produced by the APPLIED rate, not the ECB one
    assert net_eur == 1000.0


def test_eur_line_reference_is_one_eur_source(loaded_with_ecb):
    net_eur, fx, ecb_rate, ecb_date, src = _fetch_ecb(loaded_with_ecb, "TFC")
    assert src == "eur"
    assert ecb_rate == 1.0
    assert ecb_date == "2026-05-11"           # the line's own date, no lookup


def test_uncovered_non_eur_line_stores_none_not_false_pass(loaded):
    # `loaded` has NO seeded ecb_rates.db -> rate_for returns (None,None) for PLN.
    net_eur, fx, ecb_rate, ecb_date, src = _fetch_ecb(loaded, "BP")
    assert src == "none"
    assert ecb_rate is None and ecb_date is None   # NULL, not a fabricated pass
    # the applied rate still stands (figures unchanged)
    assert fx is not None and net_eur == 1000.0


def test_ecb_reference_helper_memoizes_and_never_raises(monkeypatch):
    calls = []

    def fake_rate_for(ccy, on):
        calls.append((ccy, on))
        return (4.30, "2026-05-30")

    monkeypatch.setattr(history.ecb_rates, "rate_for", fake_rate_for)
    cache = {}
    a = history.ecb_reference("PLN", "2026-05-31", cache)
    b = history.ecb_reference("PLN", "2026-05-31", cache)   # memoized -> no 2nd call
    assert a == b == (4.30, "2026-05-30", "ecb")
    assert len(calls) == 1, calls
    # EUR short-circuits without a lookup
    assert history.ecb_reference("EUR", "2026-05-31") == (1.0, "2026-05-31", "eur")

    # a raising rate_for is swallowed -> treated as no coverage
    def boom(ccy, on):
        raise RuntimeError("db gone")
    monkeypatch.setattr(history.ecb_rates, "rate_for", boom)
    assert history.ecb_reference("SEK", "2026-05-31") == (None, None, "none")


def test_figures_unchanged_and_migration_idempotent(loaded):
    """The additive column did NOT perturb net_*/vat_*, and re-running load (which
    re-applies the migration via db_migrate) neither errors nor double-adds the column."""
    before = _fetch(loaded, "BP")
    # re-run load: migration re-applied through db_migrate's versioning (idempotent),
    # rows DELETE+re-INSERT for the period.
    history.load(PERIOD)
    after = _fetch(loaded, "BP")
    assert before == after, (before, after)

    con = sqlite3.connect(loaded)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(transactions)")]
    finally:
        con.close()
    assert cols.count("fx_rate") == 1, cols
