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
