"""
Receipt-control render path is READ-ONLY.

invoice_control.run_control(period) computes receipt-control rows + orphans and,
by default (persist=True, the CLI / monthly-close), persists the recomputed rows
into invoice_receipt_control (firing audit triggers). control_summary(period) is
the render-path entry: it returns the SAME (rows, orphans) but writes nothing and
logs no audit churn. These tests pin both halves of that contract.
"""
import vat_refund

PERIOD = "2026-05"   # the demo databases ship seeded for this close period


def _control_count(period):
    fcon = vat_refund.analytics_connect()
    try:
        return fcon.execute(
            "SELECT COUNT(*) FROM invoice_receipt_control WHERE period=?",
            (period,)).fetchone()[0]
    finally:
        fcon.close()


def _audit_count():
    fcon = vat_refund.analytics_connect()
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
    fcon = vat_refund.analytics_connect()
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
    fcon = vat_refund.analytics_connect()
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
