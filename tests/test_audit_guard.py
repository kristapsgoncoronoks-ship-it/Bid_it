"""The per-process install guard must still install triggers on first use and
keep them working (performance optimisation must not break audit logging)."""
import importlib
import sqlite3


def test_install_audit_guard_installs_then_skips(tmp_path):
    import audit
    importlib.reload(audit)
    db = str(tmp_path / "a.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")

    audit.install_audit(con, ["t"])                 # first install -> triggers created
    con.execute("INSERT INTO t (v) VALUES ('x')"); con.commit()
    assert con.execute("SELECT COUNT(*) FROM audit_log WHERE tbl='t' AND action='INSERT'"
                       ).fetchone()[0] == 1

    audit.install_audit(con, ["t"])                 # second call is a guarded no-op
    con.execute("INSERT INTO t (v) VALUES ('y')"); con.commit()
    # triggers persist, so the second insert is still logged
    assert con.execute("SELECT COUNT(*) FROM audit_log WHERE tbl='t' AND action='INSERT'"
                       ).fetchone()[0] == 2
    con.close()


def test_memory_dbs_not_cached(tmp_path):
    # two distinct :memory: DBs must each get their own triggers (no false cache hit)
    import audit
    importlib.reload(audit)
    for _ in range(2):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        audit.install_audit(con, ["t"])
        con.execute("INSERT INTO t (v) VALUES ('z')"); con.commit()
        assert con.execute("SELECT COUNT(*) FROM audit_log WHERE action='INSERT'"
                           ).fetchone()[0] == 1
        con.close()
