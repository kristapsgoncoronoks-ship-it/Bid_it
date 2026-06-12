"""Migration-version table: ALTERs run once per database and are recorded, instead of
being retried behind `except OperationalError: pass` on every connect."""
import sqlite3

import db_migrate


def _con(tmp_path, name="m.db"):
    con = sqlite3.connect(str(tmp_path / name))
    con.execute("CREATE TABLE t (a)")
    return con


def test_apply_runs_once_and_records(tmp_path):
    con = _con(tmp_path)
    stmts = ["ALTER TABLE t ADD COLUMN b TEXT", "ALTER TABLE t ADD COLUMN c REAL"]
    assert db_migrate.apply(con, "mod", stmts) == 2
    cols = [r[1] for r in con.execute("PRAGMA table_info(t)")]
    assert cols == ["a", "b", "c"]
    # second call: nothing new (in-process fast path)
    assert db_migrate.apply(con, "mod", stmts) == 0
    # a fresh process (cache cleared) still skips via the migration table
    db_migrate._DONE.clear()
    assert db_migrate.apply(con, "mod", stmts) == 0
    assert con.execute("SELECT COUNT(*) FROM _ffs_migrations WHERE module='mod'").fetchone()[0] == 2


def test_apply_appends_new_statements(tmp_path):
    con = _con(tmp_path)
    db_migrate.apply(con, "mod", ["ALTER TABLE t ADD COLUMN b TEXT"])
    db_migrate._DONE.clear()
    # the list grows over time; only the NEW tail statement runs
    n = db_migrate.apply(con, "mod", ["ALTER TABLE t ADD COLUMN b TEXT",
                                      "ALTER TABLE t ADD COLUMN d TEXT"])
    assert n == 1
    assert "d" in [r[1] for r in con.execute("PRAGMA table_info(t)")]


def test_pre_existing_column_tolerated(tmp_path):
    """A DB created before the migration table (column already there) just records it."""
    con = _con(tmp_path)
    con.execute("ALTER TABLE t ADD COLUMN b TEXT")          # column pre-exists
    assert db_migrate.apply(con, "mod", ["ALTER TABLE t ADD COLUMN b TEXT"]) == 1
    db_migrate._DONE.clear()
    assert db_migrate.apply(con, "mod", ["ALTER TABLE t ADD COLUMN b TEXT"]) == 0
