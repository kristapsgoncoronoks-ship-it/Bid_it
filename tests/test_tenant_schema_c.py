"""
MULTI-TENANT P1 (schema plumbing) — security.db, the per-tenant PLATFORM stores.

Final P1 slice (audit findings G5/G6: a log/key view must never leak another
tenant's data). Stamps tenant_id onto the FOUR per-tenant platform tables so P2 can
scope the admin log/key views:

  - error_log, login_log   (auth.py db_migrate "auth" list)
  - api_keys, api_usage     (api_keys.py db_migrate "api_keys" list)

DELIBERATELY NOT stamped (platform-global, by design):
  - app_settings   (global config)
  - role_permissions (global role definitions)
  - tenants        (the registry itself)
  - users          (DEFERRED to P4 — username->tenant mapping is a design decision)

PURE PLUMBING, ZERO behavior change: the column is TEXT NOT NULL DEFAULT 'default'
(tenancy.tenant_column_ddls); the INSERTs are all explicit-column, so rows take the
DEFAULT. NOTHING SELECTs/filters the column (scope_clause is wired in P2), multitenant
is OFF, and the auth path (login/verify/roles/lockout) + the API token verify/scope
path stay byte-identical. recent_errors() does SELECT * (a column-set contract for the
admin view), so it POPs the inert tenant_id from its surfaced dicts.

These tests stand up a temp security.db (pointing auth.DB at a tmp file and clearing
the per-module _SCHEMA_READY caches), drive the writer paths, and assert: the column
lands with the right DEFAULT, rows stamp 'default', the deliberately-excluded tables
have NO tenant_id, the auth/api read contracts are unaffected, and a second connect is
idempotent (no re-ALTER, no error).
"""
import os
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import api_keys   # noqa: E402
import auth        # noqa: E402
import tenancy     # noqa: E402


def _col(con, table, name):
    """Return the PRAGMA table_info row (as a dict) for `name`, or None."""
    for r in con.execute(f"PRAGMA table_info({table})"):
        # (cid, name, type, notnull, dflt_value, pk)
        if r[1] == name:
            return {"type": r[2], "notnull": r[3], "default": r[4]}
    return None


def _assert_tenant_column(con, table):
    """The tenant column lands as TEXT NOT NULL DEFAULT 'default' on `table`."""
    c = _col(con, table, tenancy.TENANT_COLUMN)
    assert c is not None, f"{table}.{tenancy.TENANT_COLUMN} missing"
    assert c["type"].upper() == "TEXT", c
    assert c["notnull"] == 1, c
    assert tenancy.DEFAULT_TENANT_ID in (c["default"] or ""), c


def _assert_no_tenant_column(con, table):
    assert _col(con, table, tenancy.TENANT_COLUMN) is None, \
        f"{table} unexpectedly has {tenancy.TENANT_COLUMN} (out of scope for P1)"


@pytest.fixture()
def sec_db(tmp_path, monkeypatch):
    """Point auth.DB at a throwaway security.db and clear every per-module schema
    cache so connect() re-runs its CREATE/migrations against the fresh file. Restores
    nothing on the original DB (auth.DB is monkeypatched; the real file is untouched)."""
    db = str(tmp_path / "security.db")
    monkeypatch.setattr(auth, "DB", db, raising=True)
    auth._SCHEMA_READY.clear()
    api_keys._SCHEMA_READY.clear()
    tenancy._SCHEMA_READY.clear()
    return db


def _ro(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# auth.py — error_log, login_log
# ---------------------------------------------------------------------------
def test_error_log_has_tenant_column_default(sec_db):
    """After auth.connect(), error_log carries tenant_id TEXT NOT NULL DEFAULT
    'default' and a logged error row is stamped 'default' (explicit-column INSERT)."""
    auth.connect().close()
    auth.log_error("ctx", "ValueError", "boom", detail="trace", user="alice")
    con = _ro(sec_db)
    try:
        _assert_tenant_column(con, "error_log")
        rows = con.execute("SELECT tenant_id FROM error_log").fetchall()
        assert rows, "no error row logged"
        assert all(r["tenant_id"] == tenancy.DEFAULT_TENANT_ID for r in rows)
    finally:
        con.close()


def test_login_log_has_tenant_column_default(sec_db):
    """After auth.connect(), login_log carries tenant_id defaulting 'default' and a
    login attempt (verify) stamps the row 'default' (explicit-column INSERT)."""
    auth.add_user("bob", "pw-correct-horse", "processor")
    assert auth.verify("bob", "pw-correct-horse") is True   # auth path still works
    con = _ro(sec_db)
    try:
        _assert_tenant_column(con, "login_log")
        rows = con.execute(
            "SELECT tenant_id FROM login_log WHERE username=?", ("bob",)).fetchall()
        assert rows, "no login row recorded"
        assert all(r["tenant_id"] == tenancy.DEFAULT_TENANT_ID for r in rows)
    finally:
        con.close()


def test_recent_errors_excludes_tenant_id(sec_db):
    """recent_errors() does SELECT * (a column-set contract for the admin error view),
    so it POPs the inert P1 tenant_id from each surfaced dict — the admin view contract
    is byte-identical to before P1."""
    auth.log_error("ctx", "RuntimeError", "msg", detail="d", user="carol")
    errs = auth.recent_errors(50)
    assert errs, "no errors surfaced"
    for er in errs:
        assert tenancy.TENANT_COLUMN not in er, er
    # the legacy contract keys are all still present
    assert {"ts", "username", "context", "etype", "message", "detail"} <= set(errs[0])


def test_auth_path_unaffected(sec_db):
    """The auth path is byte-identical: a wrong password fails, lockout counting reads
    login_log unaffected, and roles/permissions resolve as before."""
    auth.add_user("dave", "right-pass", "processor")
    assert auth.verify("dave", "wrong-pass") is False
    assert auth.verify("dave", "right-pass") is True
    # lockout counter reads login_log (no tenant filter) and still counts
    assert auth.recent_failures("dave") == 0   # last attempt succeeded -> window reset
    assert auth.has_perm("processor", "data_import") is True
    assert auth.has_perm("admin", "user_admin") is True


# ---------------------------------------------------------------------------
# api_keys.py — api_keys, api_usage
# ---------------------------------------------------------------------------
def test_api_keys_tables_have_tenant_column_default(sec_db):
    """After api_keys.connect(), api_keys AND api_usage carry tenant_id defaulting
    'default'; an issued key + a usage row are stamped 'default' (explicit INSERTs)."""
    api_keys.connect().close()
    kid, token = api_keys.issue("lbl", ["api:benchmark"], owner="admin")
    api_keys.log_usage(kid, "/api/v1/benchmark", 200)
    con = _ro(sec_db)
    try:
        _assert_tenant_column(con, "api_keys")
        _assert_tenant_column(con, "api_usage")
        kr = con.execute("SELECT tenant_id FROM api_keys WHERE id=?", (kid,)).fetchall()
        assert kr and all(r["tenant_id"] == tenancy.DEFAULT_TENANT_ID for r in kr)
        ur = con.execute("SELECT tenant_id FROM api_usage WHERE key_id=?", (kid,)).fetchall()
        assert ur and all(r["tenant_id"] == tenancy.DEFAULT_TENANT_ID for r in ur)
    finally:
        con.close()


def test_api_token_verify_scope_path_unaffected(sec_db):
    """The token verify/scope path is byte-identical: a valid token resolves to its key
    with its scope set, verify() does NOT surface tenant_id (named-column SELECT), and a
    revoked token fails."""
    kid, token = api_keys.issue("lbl", ["api:benchmark"], owner="admin")
    key = api_keys.verify(token)
    assert key is not None
    assert tenancy.TENANT_COLUMN not in key, key   # named-column SELECT, not SELECT *
    assert api_keys.has_scope(key, "api:benchmark") is True
    api_keys.revoke(kid)
    assert api_keys.verify(token) is None           # revoked -> 401
    # list_keys is a named-column contract too
    for row in api_keys.list_keys():
        assert tenancy.TENANT_COLUMN not in row, row


# ---------------------------------------------------------------------------
# deliberate scoping — out-of-scope tables get NO tenant_id
# ---------------------------------------------------------------------------
def test_excluded_tables_have_no_tenant_column(sec_db):
    """users (deferred to P4), app_settings (global config) and role_permissions
    (global role defs) are platform-global, NOT per-tenant — they must NOT get
    tenant_id in P1. This proves the deliberate scoping."""
    auth.connect().close()
    api_keys.connect().close()
    con = _ro(sec_db)
    try:
        _assert_no_tenant_column(con, "users")
        _assert_no_tenant_column(con, "app_settings")
        _assert_no_tenant_column(con, "role_permissions")
        # the registry itself is not self-scoped
        _assert_no_tenant_column(con, "tenants") if _col(con, "tenants", "tenant_id") else None
    finally:
        con.close()


def test_idempotent_second_connect(sec_db):
    """A second connect (caches re-cleared to force the schema branch again) must NOT
    re-run the ALTERs (db_migrate is versioned) — no error, columns still present."""
    auth.connect().close()
    api_keys.connect().close()
    # force connect() back into its schema/migration branch on the SAME db file
    auth._SCHEMA_READY.clear()
    api_keys._SCHEMA_READY.clear()
    auth.connect().close()
    api_keys.connect().close()
    con = _ro(sec_db)
    try:
        for t in ("error_log", "login_log", "api_keys", "api_usage"):
            _assert_tenant_column(con, t)
    finally:
        con.close()
