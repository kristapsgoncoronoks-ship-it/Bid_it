"""
Receipt-control render path is READ-ONLY.

invoice_control.run_control(period) computes receipt-control rows + orphans and,
by default (persist=True, the CLI / monthly-close), persists the recomputed rows
into invoice_receipt_control (firing audit triggers). control_summary(period) is
the render-path entry: it returns the SAME (rows, orphans) but writes nothing and
logs no audit churn. These tests pin both halves of that contract.
"""
import invoice_control

PERIOD = "2026-05"   # the demo databases ship seeded for this close period

# invoice_receipt_control + its audit_log live in fuel_history.db, the engine-owned
# product DB. The app reads it read-only (via dataproduct); the persisting writer and
# these test fixtures use the engine-side read-WRITE handle (_control_writer).


def _control_count(period):
    fcon = invoice_control._control_writer()
    try:
        return fcon.execute(
            "SELECT COUNT(*) FROM invoice_receipt_control WHERE period=?",
            (period,)).fetchone()[0]
    finally:
        fcon.close()


def _audit_count():
    fcon = invoice_control._control_writer()
    try:
        # the table may not exist until the first persisting run installs triggers
        if not fcon.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name='audit_log'").fetchone():
            return 0
        return fcon.execute(
            "SELECT COUNT(*) FROM audit_log WHERE tbl='invoice_receipt_control'"
        ).fetchone()[0]
    finally:
        fcon.close()


def test_summary_matches_writing_entry():
    """Read-only output is IDENTICAL to the writing entry's output."""
    import invoice_control as IC
    ro_rows, ro_orphans = IC.control_summary(PERIOD)
    rw_rows, rw_orphans = IC.run_control(PERIOD)          # persist=True
    assert ro_rows == rw_rows
    assert ro_orphans == rw_orphans
    # restore demo-DB churn the persisting run just made
    import subprocess, os
    WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(["git", "restore", "fuel_history.db"], cwd=WORKDIR, check=False)


def test_summary_honors_stored_waiver():
    """A stored waived=1 override on a REAL-period row must leave both entries'
    output identical.

    The returned rows are always the freshly recomputed status (the waiver only
    governs what the WRITING path persists, never what either path returns). So
    the contract we pin is: with a waived row sitting in the live table for this
    period, the read-only entry returns EXACTLY the same rows as the writing entry
    (and the writing path does not clobber the waived row's stored status)."""
    import invoice_control as IC
    fcon = IC._control_writer()
    # establish the baseline rows for the real period, then pick one to waive.
    IC.run_control(PERIOD)
    target = fcon.execute("""SELECT supplier, country, slot, status FROM
        invoice_receipt_control WHERE period=? ORDER BY supplier, country, slot
        LIMIT 1""", (PERIOD,)).fetchone()
    assert target is not None
    # set a sentinel status + waived=1 on a genuine row of THIS period.
    fcon.execute("""UPDATE invoice_receipt_control
        SET status='WAIVED-SENTINEL', waived=1
        WHERE period=? AND supplier=? AND country=? AND slot=?""",
        (PERIOD, target["supplier"], target["country"], target["slot"]))
    fcon.commit(); fcon.close()

    # read-only vs writing entry: identical returned rows (both recomputed).
    ro_rows, _ = IC.control_summary(PERIOD)
    rw_rows, _ = IC.run_control(PERIOD)
    assert ro_rows == rw_rows

    # the writing path must preserve the waived row's stored status (ON CONFLICT
    # CASE keeps it when waived=1), so the manual waiver survives the persist.
    fcon = IC._control_writer()
    kept = fcon.execute("""SELECT status, waived FROM invoice_receipt_control
        WHERE period=? AND supplier=? AND country=? AND slot=?""",
        (PERIOD, target["supplier"], target["country"], target["slot"])).fetchone()
    fcon.close()
    assert kept["waived"] == 1 and kept["status"] == "WAIVED-SENTINEL"

    import subprocess, os
    WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(["git", "restore", "fuel_history.db"], cwd=WORKDIR, check=False)


def test_summary_does_not_write_or_audit():
    """control_summary writes no rows and adds no audit entries; run_control does."""
    import invoice_control as IC
    # prime the table so the count is stable to start from
    IC.run_control(PERIOD)
    before_rows = _control_count(PERIOD)
    before_audit = _audit_count()

    # read-only: must not change the row count or the audit log for this table
    IC.control_summary(PERIOD)
    assert _control_count(PERIOD) == before_rows
    assert _audit_count() == before_audit

    # writing entry: re-touches the rows (count unchanged on ON CONFLICT, but it
    # does write — verified separately) and is the persisting path.
    IC.run_control(PERIOD)
    assert _control_count(PERIOD) == before_rows   # upsert, same key set

    import subprocess, os
    WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(["git", "restore", "fuel_history.db"], cwd=WORKDIR, check=False)


def test_read_path_tolerates_missing_control_table(tmp_path, monkeypatch):
    """Fresh post-history deployment: fuel_history.db has `transactions` but the
    invoice_control CLI has NEVER created invoice_receipt_control. The render path
    (persist=False) opens the product DB READ-ONLY, so it must NOT try to create the
    table — it computes its rows from transactions/cadence and returns them without
    raising. run_control(persist=True) then owns table creation + persists.

    Regression guard for the D1 read-boundary: pre-fix, control_summary ran
    `CREATE TABLE IF NOT EXISTS` on the read-only handle and raised
    `sqlite3.OperationalError: attempt to write a readonly database`.
    """
    import sqlite3
    import invoice_control as IC
    import dataproduct

    db_path = str(tmp_path / "fuel_history.db")
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE transactions (
        period TEXT NOT NULL,
        entity TEXT, supplier TEXT, country TEXT, vehicle TEXT,
        date TEXT, time TEXT, station TEXT, product TEXT, product_group TEXT,
        qty REAL, currency TEXT, net_local REAL, vat_local REAL, gross_local REAL,
        net_eur REAL, vat_eur REAL, net_eur_eff REAL, note TEXT)""")
    # seed one VAT-bearing line so the cadence logic produces at least one row
    con.execute("""INSERT INTO transactions
        (period, supplier, country, date, qty) VALUES (?,?,?,?,?)""",
        (PERIOD, "BP", "Germany", f"{PERIOD}-05", 100.0))
    con.commit(); con.close()
    # sanity: the control table is genuinely absent on this fresh DB
    chk = sqlite3.connect(db_path)
    assert chk.execute("SELECT name FROM sqlite_master WHERE type='table' "
                       "AND name='invoice_receipt_control'").fetchone() is None
    chk.close()

    # point BOTH the engine writer path and the read-only dataproduct accessor at
    # the temp DB (suppliers.db / vat_claims.db stay the demo DBs).
    monkeypatch.setattr(IC, "FUEL_HISTORY_DB", db_path)
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", db_path)

    # READ path must NOT raise on the missing table and must return computed rows.
    rows, orphans = IC.control_summary(PERIOD)
    assert isinstance(rows, list) and rows, "read path returned no computed rows"
    # nothing got written: the table is still absent after the read-only render.
    chk = sqlite3.connect(db_path)
    assert chk.execute("SELECT name FROM sqlite_master WHERE type='table' "
                       "AND name='invoice_receipt_control'").fetchone() is None
    chk.close()

    # WRITE path owns creation + persistence.
    rw_rows, _ = IC.run_control(PERIOD, persist=True)
    assert rw_rows == rows               # same computed rows either way
    chk = sqlite3.connect(db_path)
    assert chk.execute("SELECT name FROM sqlite_master WHERE type='table' "
                       "AND name='invoice_receipt_control'").fetchone() is not None
    assert chk.execute("SELECT COUNT(*) FROM invoice_receipt_control "
                       "WHERE period=?", (PERIOD,)).fetchone()[0] == len(rw_rows)
    chk.close()


def test_invoices_page_does_not_write(client):
    """A GET to /invoices (render path) must not grow invoice_receipt_control."""
    import invoice_control as IC
    IC.run_control(PERIOD)                 # establish a baseline row set
    before = _control_count(PERIOD)
    before_audit = _audit_count()

    r = client.get(f"/invoices?period={PERIOD}")
    assert r.status_code == 200
    assert _control_count(PERIOD) == before
    assert _audit_count() == before_audit

    import subprocess, os
    WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(["git", "restore", "fuel_history.db"], cwd=WORKDIR, check=False)
