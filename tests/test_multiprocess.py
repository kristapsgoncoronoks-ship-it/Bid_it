"""Multi-process readiness: WAL tuning on connections, and the cross-process
guard that stops two processes backing up at once."""
import importlib
import os
import sqlite3


def test_dbtune_sets_wal(tmp_path):
    import db_tuning
    db = str(tmp_path / "t.db")
    con = sqlite3.connect(db)
    db_tuning.tune(con)
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    busy = con.execute("PRAGMA busy_timeout").fetchone()[0]
    con.close()
    assert mode.lower() == "wal"
    assert busy >= 1000


def test_module_connections_are_wal(tmp_path, monkeypatch):
    # a real module connection (customer_master) should come up in WAL for concurrent
    # multi-process access.
    import customer_master
    importlib.reload(customer_master)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "customers.db"))
    customer_master._SCHEMA_READY.clear()
    con = customer_master.connect()
    assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    con.close()


def test_backup_is_single_writer_across_processes(tmp_path, monkeypatch):
    # run_backup_now must refuse to start a second snapshot while another "process"
    # holds the cross-process backup lock.
    import process_lock
    importlib.reload(process_lock)
    monkeypatch.setattr(process_lock, "DB", str(tmp_path / "locks.db"))
    process_lock._READY.clear()

    import app as A
    # simulate another process already running a backup
    assert process_lock.acquire("backup-run", ttl=600, holder="other-host:999") is True
    try:
        raised = False
        try:
            A.run_backup_now()
        except RuntimeError as e:
            raised = True
            assert "already running" in str(e)
        assert raised, "expected run_backup_now to refuse while the lock is held"
    finally:
        process_lock.release("backup-run", "other-host:999")
