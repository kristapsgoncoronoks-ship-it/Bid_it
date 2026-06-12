"""
PROCESS LOCK — a tiny cross-process advisory lock (lease) backed by SQLite.

When the app runs as several worker processes, some jobs must NOT run in every
process at once (e.g. the scheduled-backup loop, or any "only one at a time"
operation). This gives them a named, time-limited lease that exactly one process
can hold. No new dependencies, cross-platform (no flock/fcntl), survives crashes
because the lease simply EXPIRES — a process that dies holding a lock blocks
nobody once `ttl` elapses.

Semantics:
    acquire(name, ttl, holder)  -> True if you now hold `name` for ttl seconds.
                                   Re-acquiring as the same holder RENEWS it.
    release(name, holder)       -> drop it (only if you hold it).
    held_by(name)               -> current holder or None (None if expired).

Atomicity comes from a single BEGIN IMMEDIATE transaction (SQLite's one-writer
rule), so two processes racing to acquire the same free lock can't both win.

Typical leader-election loop (see app.start_backup_scheduler):
    me = process_lock.whoami()
    while True:
        if process_lock.acquire("backup-scheduler", ttl=2*PERIOD, holder=me):
            ...do the singleton work, renewing the lease each iteration...
        time.sleep(PERIOD)
"""
import os, socket, sqlite3, time

import db

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("PROCLOCK_DB", f"{WORKDIR}/locks.db")

_READY = set()


def whoami():
    """A stable id for THIS process across the machine."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _connect():
    con = sqlite3.connect(DB, timeout=30)
    if DB not in _READY:
        con.execute("""CREATE TABLE IF NOT EXISTS proc_locks (
            name TEXT PRIMARY KEY, holder TEXT, expires REAL)""")
        try: con.execute("PRAGMA journal_mode=WAL")
        except db.DBError: pass
        con.execute("PRAGMA busy_timeout=15000")
        con.commit()
        _READY.add(DB)
    return con


def acquire(name, ttl, holder=None):
    """Take or renew the lease `name` for `ttl` seconds. Returns True on success."""
    holder = holder or whoami()
    now = time.time()
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT holder, expires FROM proc_locks WHERE name=?",
                          (name,)).fetchone()
        free = (row is None) or (row[1] is None) or (row[1] < now) or (row[0] == holder)
        if free:
            con.execute("""INSERT INTO proc_locks (name, holder, expires) VALUES (?,?,?)
                           ON CONFLICT(name) DO UPDATE SET holder=excluded.holder,
                           expires=excluded.expires""",
                        (name, holder, now + ttl))
            con.execute("COMMIT")
            return True
        con.execute("COMMIT")
        return False
    except db.DBError:
        # could not get the write lock in time — treat as "not acquired"
        try: con.execute("ROLLBACK")
        except db.DBError: pass
        return False
    finally:
        con.close()


def release(name, holder=None):
    """Release `name` if held by `holder` (default: this process)."""
    holder = holder or whoami()
    con = _connect()
    try:
        con.execute("DELETE FROM proc_locks WHERE name=? AND holder=?", (name, holder))
        con.commit()
    finally:
        con.close()


def held_by(name):
    """Current holder of `name`, or None if free/expired."""
    con = _connect()
    try:
        row = con.execute("SELECT holder, expires FROM proc_locks WHERE name=?",
                          (name,)).fetchone()
    finally:
        con.close()
    if not row or row[1] is None or row[1] < time.time():
        return None
    return row[0]


if __name__ == "__main__":
    # offline self-test
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "locks.db"); _READY.clear()
    assert acquire("job", 60, "A") is True
    assert acquire("job", 60, "B") is False          # B can't steal A's live lease
    assert acquire("job", 60, "A") is True           # A renews its own
    assert held_by("job") == "A"
    # expired lease is free to take
    assert acquire("short", 0.01, "A") is True
    time.sleep(0.05)
    assert held_by("short") is None
    assert acquire("short", 60, "B") is True
    release("job", "A")
    assert held_by("job") is None
    print("process_lock self-test OK")
