"""
Horizontal-scaling readiness — the pieces that make a multi-server deployment
config-only (see docs/SCALING.md):

  * db.qmark_to_pyformat  — the SQLite-qmark -> psycopg-pyformat paramstyle shim
                            (the documented #1 blocker to the Postgres cutover).
  * auth.secret_key       — FFS_SECRET_KEY env override so all web nodes share one
                            signing key (signed-cookie sessions, no sticky sessions).
  * app.node_role         — FFS_ROLE web/worker/all, so web nodes don't drain the queue.

These run on the default SQLite engine; the Postgres wiring is validated by
construction (no live Postgres in CI) — only the pure translator is exercised here.
"""
import importlib
import os

import pytest

import db


# --------------------------------------------------------------- paramstyle shim
def test_qmark_basic():
    assert db.qmark_to_pyformat("SELECT * FROM t WHERE a=? AND b=?") == \
        "SELECT * FROM t WHERE a=%s AND b=%s"


def test_qmark_no_params_unchanged():
    assert db.qmark_to_pyformat("SELECT 1") == "SELECT 1"


def test_qmark_inside_string_literal_left_alone():
    # a literal '?' inside quotes is data, not a placeholder
    assert db.qmark_to_pyformat("SELECT '?' , x WHERE y=?") == "SELECT '?' , x WHERE y=%s"


def test_qmark_escaped_quote_in_literal():
    # '' is an escaped single quote — the literal does not end there
    sql = "INSERT INTO t VALUES ('O''Brien', ?)"
    assert db.qmark_to_pyformat(sql) == "INSERT INTO t VALUES ('O''Brien', %s)"


def test_qmark_literal_percent_is_doubled():
    # pyformat treats % specially, so a LIKE pattern's % must be escaped
    sql = "SELECT * FROM t WHERE name LIKE '%x%' AND id=?"
    assert db.qmark_to_pyformat(sql) == "SELECT * FROM t WHERE name LIKE '%%x%%' AND id=%s"


def test_qmark_question_mark_in_quoted_with_following_param():
    sql = "UPDATE t SET note='ok?' WHERE id=? AND k=?"
    assert db.qmark_to_pyformat(sql) == "UPDATE t SET note='ok?' WHERE id=%s AND k=%s"


# ---------------------------------------------------------------- db_tuning.tune
def test_tune_noop_on_non_sqlite_engine(monkeypatch):
    # Under Postgres, tune() must early-return WITHOUT issuing any PRAGMA — the
    # first PRAGMA would otherwise crash. It returns the connection unchanged.
    import db_tuning
    monkeypatch.setattr(db, "ENGINE", "postgres")

    class _Sentinel:
        def execute(self, *a, **k):
            raise AssertionError("tune() must issue no PRAGMA when ENGINE!=sqlite")

    sentinel = _Sentinel()
    assert db_tuning.tune(sentinel) is sentinel


def test_tune_sets_wal_on_sqlite(monkeypatch):
    # Positive control: the SQLite path still applies the PRAGMAs as before.
    import sqlite3
    import db_tuning
    monkeypatch.setattr(db, "ENGINE", "sqlite")
    con = sqlite3.connect(":memory:")
    try:
        db_tuning.tune(con, busy_ms=12345)
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 12345
        # :memory: reports "memory" for journal_mode (WAL is ignored transparently),
        # but the PRAGMA must have executed without raising.
        assert con.execute("PRAGMA journal_mode").fetchone()[0] is not None
    finally:
        con.close()


# ------------------------------------------------------------------ secret key
def test_secret_key_env_override(monkeypatch):
    import auth
    monkeypatch.setenv("FFS_SECRET_KEY", "shared-across-all-web-nodes")
    assert auth.secret_key() == b"shared-across-all-web-nodes"


def test_secret_key_falls_back_to_file(monkeypatch):
    import auth
    monkeypatch.delenv("FFS_SECRET_KEY", raising=False)
    key = auth.secret_key()
    assert isinstance(key, bytes) and len(key) >= 16   # the generated/read file key


# ------------------------------------------------------------------- node role
def test_node_role_default_is_all(monkeypatch):
    import app as A
    monkeypatch.delenv("FFS_ROLE", raising=False)
    assert A.node_role() == "all"


def test_node_role_reads_env(monkeypatch):
    import app as A
    monkeypatch.setenv("FFS_ROLE", "  WEB ")
    assert A.node_role() == "web"


def test_web_node_does_not_start_intake_worker(monkeypatch):
    import app as A
    monkeypatch.setenv("FFS_ROLE", "web")
    monkeypatch.setattr(A, "_intake_started", False, raising=False)
    started = {"flag": False}

    class _FakeThread:
        def __init__(self, *a, **k): pass
        def start(self): started["flag"] = True

    monkeypatch.setattr(A.threading, "Thread", _FakeThread)
    A.start_intake_worker()
    assert started["flag"] is False, "web node must not spawn the intake worker"
    assert A._intake_started is False


def test_all_node_starts_intake_worker(monkeypatch):
    import app as A
    monkeypatch.setenv("FFS_ROLE", "all")
    monkeypatch.delenv("INTAKE_WORKER", raising=False)
    monkeypatch.setattr(A, "_intake_started", False, raising=False)
    started = {"flag": False}

    class _FakeThread:
        def __init__(self, *a, **k): pass
        def start(self): started["flag"] = True

    monkeypatch.setattr(A.threading, "Thread", _FakeThread)
    try:
        A.start_intake_worker()
        assert started["flag"] is True, "all-role node must spawn the intake worker"
    finally:
        A._intake_started = False     # don't leak state into other tests
