"""
The data-processing engine OWNS fuel_history.db; its canonical WRITER (history.py)
must tune the file to WAL, exactly like the app/consumer read paths
(pricing_intelligence.connect, vat_refund.analytics_connect). Mixed WAL/rollback on
one file risks reader/writer contention on close, so every handle must agree.

These tests assert the writer leaves the file in WAL mode and that running the
history loader does not regress that. SQLite-only (PRAGMAs are no-ops elsewhere).
"""
import os, sys, subprocess, sqlite3
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import history

pytestmark = pytest.mark.skipif(db.ENGINE != "sqlite",
                                reason="WAL PRAGMAs are SQLite-only")


def _journal_mode(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA journal_mode").fetchone()[0].lower()
    finally:
        con.close()


def test_history_db_is_wal_after_load():
    """Running the history loader (the engine writer) leaves fuel_history.db in WAL.
    history.py loads consolidated_rows.pkl into transactions and tunes its writer
    connection; afterwards the persistent journal mode on the file is 'wal'."""
    pkl = os.path.join(history.WORKDIR, "consolidated_rows.pkl")
    if not os.path.exists(pkl):
        pytest.skip("consolidated_rows.pkl not present (run consolidate.py first)")
    r = subprocess.run([sys.executable, history.__file__],
                       cwd=history.WORKDIR, capture_output=True, text=True)
    assert r.returncode == 0, f"history.py failed: {r.stderr}"
    assert _journal_mode(history.DB) == "wal"


def test_history_writer_tunes_connection():
    """history.py imports db_tuning and tunes its writer handle right after connect
    (so the engine writer matches the consumer connections' WAL setting)."""
    src = open(history.__file__, encoding="utf-8").read()
    assert "import db_tuning" in src
    assert "db_tuning.tune(con)" in src
