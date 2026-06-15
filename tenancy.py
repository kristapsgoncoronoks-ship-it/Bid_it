"""
TENANCY LAYER — the FOUNDATION mechanism for the multi-tenant SaaS program (P0).

This module is the bounded, safe, zero-impact first slice of multi-tenancy. It
ships the two primitives every later phase composes on:

  1. a tenant REGISTRY (who the tenants are), and
  2. a request-scoped tenant CONTEXT (which tenant THIS unit of work belongs to),

plus the enforcement HELPERS (`require_tenant`, `scope_clause`) that the phased
per-table work (P2) will apply — but that NOTHING in this slice wires into an
existing product query. See docs/MULTI_TENANCY.md for the full program plan.

CARDINAL INVARIANT — OFF BY DEFAULT = BYTE-IDENTICAL EXISTING BEHAVIOR.
The `multitenant` app setting defaults to "0" (OFF). While OFF:
  - `current_tenant()` has no tenant to resolve and stays inert,
  - `scope_clause()` returns ("", []) — a literal no-op that changes no SQL,
  - `require_tenant()` does NOT raise.
So a default single-tenant install behaves EXACTLY as today; this module adds a
registry table and inert helpers and touches no existing query, route, or figure.

REGISTRY HOME — security.db. The tenant registry is app-owned platform metadata,
not an engine PRODUCT table, so it belongs with the other app-owned platform
tables (users, app_settings, error_log) in security.db via auth.connect(). This
keeps it inside the existing security-DB backup/permission/audit envelope and
avoids introducing a new DB file (and its .gitignore/backup wiring). It is
DELIBERATELY not in fuel_history.db/suppliers.db (engine product DBs the app
holds no writable handle to) nor benchmark.db (app data, but not platform meta).

CONTEXT — mirrors audit.py's proven thread-local ACTOR pattern. Under a threaded
WSGI server each request is served on its own worker thread, so each thread sees
its own tenant with zero cross-request interleave. The app's before/after hooks
set/reset it per request exactly as they do the audit actor.

Never `except: pass`; the read path (current_tenant / scope_clause / get_tenant /
list_tenants) never raises — a broken or missing registry degrades to "no tenant"
/ empty, logged, rather than taking down a request.
"""
import os
import sqlite3
import threading

import applog
import db_migrate

log = applog.get("tenancy")

WORKDIR = os.path.dirname(os.path.abspath(__file__))

# The setting that ARMS the whole program. Stored in app_settings (security.db)
# like every other module switch; "0"/absent = OFF (single-tenant, inert).
SETTING = "multitenant"

# ── Registry (app-owned, in security.db via auth.connect) ──────────────────────

_SCHEMA_READY = set()   # security.db paths whose tenancy schema is set up this process

_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id  TEXT PRIMARY KEY,
    name       TEXT,
    active     INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect():
    """Open the app-owned security.db (where the tenant registry lives) and make
    sure the `tenants` table exists. Reuses auth.connect() so the registry sits
    inside the same WAL/permission/audit envelope as users/app_settings. The
    schema-once guard mirrors auth.connect()'s _SCHEMA_READY."""
    import auth
    con = auth.connect()
    path = auth.DB
    if path == ":memory:" or path not in _SCHEMA_READY:
        con.executescript(_REGISTRY_DDL)
        # Versioned migrations: APPEND new statements at the END (positions stable).
        db_migrate.apply(con, "tenancy", [])
        con.commit()
        _SCHEMA_READY.add(path)
    return con


def create_tenant(tenant_id, name):
    """Register a tenant (idempotent on tenant_id — re-creating updates the name
    and re-activates). Returns the tenant row dict. Raises on a falsy id (a
    programming error, not a read-path concern)."""
    tenant_id = (tenant_id or "").strip()
    if not tenant_id:
        raise ValueError("tenant_id must be a non-empty string")
    con = connect()
    try:
        con.execute(
            """INSERT INTO tenants (tenant_id, name, active) VALUES (?,?,1)
               ON CONFLICT(tenant_id) DO UPDATE SET name=excluded.name, active=1""",
            (tenant_id, name))
        con.commit()
    finally:
        con.close()
    return get_tenant(tenant_id)


def get_tenant(tenant_id):
    """Return the tenant row as a dict, or None. Never raises (read path)."""
    if not tenant_id:
        return None
    try:
        con = connect()
        try:
            row = con.execute(
                "SELECT tenant_id, name, active, created_at FROM tenants WHERE tenant_id=?",
                (tenant_id,)).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_tenant(%r) failed, treating as unknown: %s", tenant_id, e)
        return None


def list_tenants():
    """All registered tenants (active first, then by id), as dicts. Never raises."""
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT tenant_id, name, active, created_at FROM tenants
                   ORDER BY active DESC, tenant_id""").fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("list_tenants failed, returning empty: %s", e)
        return []


def set_active(tenant_id, active):
    """Activate (active truthy) or deactivate a tenant. Returns the updated row
    (or None if unknown). Raises only on a missing id (programming error)."""
    if not tenant_id:
        raise ValueError("tenant_id must be a non-empty string")
    con = connect()
    try:
        con.execute("UPDATE tenants SET active=? WHERE tenant_id=?",
                    (1 if active else 0, tenant_id))
        con.commit()
    finally:
        con.close()
    return get_tenant(tenant_id)


# ── Request-scoped context (mirrors audit.py's thread-local ACTOR) ─────────────

_local = threading.local()


def set_tenant(tenant_id):
    """Bind subsequent work on the CURRENT thread to `tenant_id`. The app's
    before-request hook calls this (only when multitenant is ON); call
    reset_tenant() when the unit of work finishes. A falsy value clears it."""
    _local.tenant = tenant_id or None


def current_tenant():
    """The tenant bound to the current thread, or None when unset. Never raises.

    NOTE: this returns whatever was set on THIS thread regardless of the global
    switch — but because the app's request hook only set_tenant()s while
    multitenant is ON, and the enforcement helpers below (`scope_clause`,
    `require_tenant`) are themselves switch-gated, an OFF install never has a
    tenant bound and is fully inert."""
    return getattr(_local, "tenant", None)


def reset_tenant():
    """Clear the current thread's tenant (after-request hook)."""
    _local.tenant = None


# ── The master switch ──────────────────────────────────────────────────────────

def multitenant_enabled():
    """True iff the `multitenant` setting is ON. Defaults to OFF ("0"). Never
    raises — a registry/settings read failure degrades to OFF (the safe,
    single-tenant default) rather than taking down a request."""
    try:
        import auth
        val = (auth.get_setting(SETTING, "0") or "0").strip().lower()
        return val in ("1", "true", "yes", "on")
    except Exception as e:
        log.warning("multitenant_enabled read failed, defaulting OFF: %s", e)
        return False


# ── Enforcement primitives (used by the FUTURE per-table phase, NOT yet wired) ──

def require_tenant():
    """Return the current tenant, or raise if multitenant is ON and none is set.

    This is an enforcement PRIMITIVE for the per-query scoping phase (P2): a
    tenant-scoped write/read that genuinely needs a tenant calls this to fail
    LOUD rather than silently touch the wrong (or every) tenant's data. When
    multitenant is OFF it is inert and returns None. NOT yet wired into any
    existing query in this slice."""
    if not multitenant_enabled():
        return None
    t = current_tenant()
    if t is None:
        raise RuntimeError(
            "require_tenant(): multitenant is ON but no tenant is set on this "
            "request/thread — refusing to run a tenant-scoped operation unscoped.")
    return t


def scope_clause(column="tenant_id"):
    """Return an (sql_fragment, params) pair to AND into a tenant-scoped query.

    *** NOT YET APPLIED TO ANY EXISTING QUERY. *** This is the helper the phased
    per-table work (P2, docs/MULTI_TENANCY.md) will splice into each tenant-scoped
    SELECT/UPDATE/DELETE, table-by-table, EACH with a cross-tenant access test.

    Contract:
      - multitenant OFF  -> ("", [])            a literal no-op: existing SQL is
                                                 unchanged, so OFF installs behave
                                                 byte-identically to today.
      - multitenant ON   -> (" AND {col} = ?", [current_tenant()])

    The fragment leads with " AND " so it appends after an existing WHERE; the
    column name is interpolated (caller-controlled identifier, never user input)
    and the tenant VALUE is always a bound parameter. Never raises."""
    try:
        if not multitenant_enabled():
            return ("", [])
        return (f" AND {column} = ?", [current_tenant()])
    except Exception as e:
        # A failure here must never widen a scope: degrade to the OFF no-op only
        # when multitenant is provably OFF; if we can't even tell, prefer raising
        # nothing here and let require_tenant() at the call site gate the write.
        log.warning("scope_clause(%r) failed, returning no-op: %s", column, e)
        return ("", [])


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] == "list":
        for t in list_tenants():
            print(f"{t['tenant_id']:24} active={t['active']} {t.get('name') or ''}")
    elif len(args) >= 2 and args[0] == "create":
        t = create_tenant(args[1], args[2] if len(args) > 2 else args[1])
        print("created:", t)
    elif len(args) >= 2 and args[0] in ("activate", "deactivate"):
        print(set_active(args[1], args[0] == "activate"))
    else:
        print("usage: python tenancy.py [list | create <id> [name] | "
              "activate <id> | deactivate <id>]")
