"""
MIGRATION-VERSION TABLE — run schema ALTERs once per database instead of guessing with
`try: ALTER … except OperationalError: pass` on every connect().

    import db_migrate
    db_migrate.apply(con, "vat_refund", ["ALTER TABLE x ADD COLUMN y", ...])

Each statement is identified by (module, position) in `_ffs_migrations`. On the first
encounter it is executed — still tolerating "duplicate column" for databases created
before this table existed — and recorded; afterwards a single indexed SELECT skips the
whole list. Append new statements at the END of a module's list (positions are stable).
Unexpected failures are LOGGED (applog), not swallowed.
"""
import sqlite3

import applog

log = applog.get("db_migrate")

_SCHEMA = """CREATE TABLE IF NOT EXISTS _ffs_migrations (
    module TEXT, idx INTEGER, statement TEXT,
    applied_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (module, idx))"""

# (module, db-path) pairs already verified this process — skips even the SELECT.
_DONE = set()


def apply(con, module, statements):
    """Apply `statements` (DDL strings) for `module` exactly once per database.
    Returns the number of statements newly applied."""
    dbkey = None
    try:
        # one key per attached main database file
        dbkey = (module, con.execute("PRAGMA database_list").fetchone()[2])
        if dbkey in _DONE:
            return 0
    except sqlite3.Error:
        pass
    con.execute(_SCHEMA)
    done = {r[0] for r in con.execute(
        "SELECT idx FROM _ffs_migrations WHERE module=?", (module,))}
    n = 0
    for i, ddl in enumerate(statements):
        if i in done:
            continue
        try:
            con.execute(ddl)
        except sqlite3.OperationalError as e:
            # pre-existing DBs already have the column/table — record and move on;
            # anything else is a real problem and is logged.
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                log.warning("%s migration %d failed (%s): %s", module, i, ddl[:80], e)
        con.execute("INSERT OR IGNORE INTO _ffs_migrations (module, idx, statement) "
                    "VALUES (?,?,?)", (module, i, ddl[:200]))
        n += 1
    if n:
        con.commit()
    if dbkey:
        _DONE.add(dbkey)
    return n
