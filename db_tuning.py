"""
DB TUNING — make SQLite safe and fast under MULTIPLE PROCESSES.

The app is designed to run as several worker processes (e.g. several waitress
instances behind a proxy, or gunicorn workers) all sharing the same .db files.
Two PRAGMAs make that work well; `tune()` applies them on every connection:

  • journal_mode=WAL — Write-Ahead Logging lets readers and a writer work
    CONCURRENTLY across processes (the default rollback journal makes a writer
    block all readers). WAL is persistent per database file; re-issuing it each
    connect is a cheap no-op once set. Creates <db>-wal / <db>-shm sidecars.
  • busy_timeout — when another process holds the write lock, wait up to this many
    milliseconds for it instead of failing immediately with "database is locked".
  • synchronous=NORMAL — the recommended durability level under WAL: safe across
    application crashes, only a power/OS crash can lose the last transaction —
    acceptable here because backups are crash-consistent and the audit log lets
    any lost tail be re-entered. (FULL would fsync on every commit; NORMAL is the
    documented WAL companion.)

Single-process (the historic default) is unaffected — these settings are correct
there too. :memory: databases ignore WAL transparently, so tests are unaffected.
"""
import sqlite3

BUSY_MS = 15000        # how long a connection waits for a contended write lock


def tune(con, busy_ms=BUSY_MS, wal=True):
    """Apply the multi-process PRAGMAs to a fresh connection. Idempotent and
    cheap; safe to call on every connect(). Never raises (a read-only filesystem
    or :memory: db simply keeps its default)."""
    try:
        con.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
        if wal:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass
    return con
