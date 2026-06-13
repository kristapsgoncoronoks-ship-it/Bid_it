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


def test_actor_attribution(tmp_path):
    # ffs_actor() resolves the thread-local actor per row, so each change is
    # attributed to whoever was active when it was written.
    import audit
    importlib.reload(audit)
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    audit.install_audit(con, ["t"])

    audit.set_actor(con, "alice")
    con.execute("INSERT INTO t (v) VALUES ('a')"); con.commit()
    audit.set_actor(con, "bob")
    con.execute("INSERT INTO t (v) VALUES ('b')"); con.commit()
    audit.reset_actor(con)
    con.execute("INSERT INTO t (v) VALUES ('c')"); con.commit()

    rows = dict(con.execute(
        "SELECT new_data, changed_by FROM audit_log WHERE action='INSERT'").fetchall())
    by_actor = {}
    import json
    for data, who in rows.items():
        by_actor[json.loads(data)["v"]] = who
    assert by_actor == {"a": "alice", "b": "bob", "c": "system"}
    con.close()


def test_actor_thread_isolation(tmp_path):
    # Two threads writing concurrently must NOT cross-attribute: each thread's
    # actor is private to that thread (no shared committed actor row).
    import audit, threading
    importlib.reload(audit)
    results = {}

    def work(name):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        audit.install_audit(con, ["t"])
        audit.set_actor(con, name)
        for i in range(50):
            con.execute("INSERT INTO t (v) VALUES (?)", (f"{name}{i}",)); con.commit()
        whos = {r[0] for r in con.execute(
            "SELECT changed_by FROM audit_log WHERE action='INSERT'")}
        results[name] = whos
        con.close()

    threads = [threading.Thread(target=work, args=(n,)) for n in ("alice", "bob", "carol")]
    for t in threads: t.start()
    for t in threads: t.join()
    # each thread only ever saw its own actor
    assert results == {"alice": {"alice"}, "bob": {"bob"}, "carol": {"carol"}}


def _seed_audit_log(con):
    con.execute("""CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP,
        tbl TEXT, rowkey TEXT, action TEXT,
        old_data TEXT, new_data TEXT, changed_by TEXT)""")
    rows = [
        ("claims", "C1", "INSERT", "amy"),
        ("claims", "C1", "UPDATE", "amy"),
        ("claims", "C1", "UPDATE", "bob"),     # C1 revised 2x -> top churn
        ("claims", "C2", "UPDATE", "amy"),
        ("claims", "C3", "DELETE", "bob"),
        ("invoices", "I1", "INSERT", None),    # NULL -> "(system)"
        ("claims", "OLD", "BASELINE", None),   # MUST be excluded
    ]
    con.executemany(
        "INSERT INTO audit_log (tbl, rowkey, action, changed_by) VALUES (?,?,?,?)", rows)
    con.commit()


def test_activity_summary_splits_and_churn(tmp_path):
    import audit
    importlib.reload(audit)
    con = sqlite3.connect(":memory:")
    _seed_audit_log(con)
    act = audit.activity_summary(con, 30)

    by_user = {u["changed_by"]: u for u in act["by_user"]}
    # amy: 1 insert, 2 updates, 0 deletes (BASELINE excluded)
    assert (by_user["amy"]["inserts"], by_user["amy"]["updates"],
            by_user["amy"]["deletes"], by_user["amy"]["total"]) == (1, 2, 0, 3)
    assert (by_user["bob"]["updates"], by_user["bob"]["deletes"]) == (1, 1)
    assert "(system)" in by_user and by_user["(system)"]["inserts"] == 1

    by_table = {t["tbl"]: t for t in act["by_table"]}
    # claims: 1 insert + 3 updates + 1 delete = 5 (BASELINE not counted)
    assert by_table["claims"]["total"] == 5
    assert by_table["invoices"]["inserts"] == 1
    # sorted by total desc
    totals = [t["total"] for t in act["by_table"]]
    assert totals == sorted(totals, reverse=True)

    # churn: C1 updated twice -> first/most
    assert act["churn"][0] == {"tbl": "claims", "rowkey": "C1", "updates": 2}
    # no BASELINE rowkey anywhere
    assert all(c["rowkey"] != "OLD" for c in act["churn"])
    con.close()


def test_activity_summary_empty_is_safe(tmp_path):
    import audit
    importlib.reload(audit)
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts TEXT,
        tbl TEXT, rowkey TEXT, action TEXT, old_data TEXT, new_data TEXT, changed_by TEXT)""")
    assert audit.activity_summary(con, 30) == {
        "days": 30, "by_user": [], "by_table": [], "churn": []}
    con.close()


def test_activity_summary_never_raises(tmp_path):
    import audit
    importlib.reload(audit)
    con = sqlite3.connect(":memory:")   # no audit_log table at all -> error path
    assert audit.activity_summary(con, 30) == {
        "days": 30, "by_user": [], "by_table": [], "churn": []}
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
