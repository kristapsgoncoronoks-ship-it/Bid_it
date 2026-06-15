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

OWNER/OPERATOR SCOPE — the deliberate, audited cross-tenant exception. Client
tenants are isolated from each other, but the platform OPERATOR (the business
owner) gets a READ-ONLY cross-tenant analytics scope (the "I must have all
analytics data" requirement). `set_owner_scope()` marks the thread as the owner;
`scope_clause()` then returns NO filter — the one place it deliberately does so
while the switch is ON. Owner and tenant are MUTUALLY EXCLUSIVE; writes ALWAYS
need a concrete tenant (`require_tenant()` raises under owner scope too). Owner
analytics MUST run on de-identified/aggregated data (PII excluded) and must never
relay one client's identifiable pricing to another — see
docs/SECURITY_COMPLIANCE_PLAN.md §7 (GDPR controller/anonymise + antitrust).

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

# ── Per-table tenant column (P1: schema plumbing, no query wired yet) ───────────

# The implicit single tenant. Every EXISTING row in a single-tenant install
# backfills to this id (via the column DEFAULT below), and every NEW row stamps
# to it too while multitenant is OFF. When the operator later goes multi-CLIENT,
# they rename/migrate this default tenant's data to a named tenant (or register
# `default` in the tenant registry and add real tenants alongside); until then it
# is the one implicit tenant and no query reads the column. Keep this value
# stable — it is baked into the column DEFAULT of every tenant-scoped table.
DEFAULT_TENANT_ID = "default"

# The tenant column name, kept here so the schema DDL and scope_clause(column=...)
# stay in lockstep across every P1 slice.
TENANT_COLUMN = "tenant_id"


def tenant_column_ddls(tables):
    """Return one ALTER-ADD-COLUMN statement per table that adds the tenant_id
    column with the DEFAULT-TENANT backfill.

    This is the REUSABLE P1 mechanism: every per-DB schema slice (customers,
    suppliers, vat_claims, benchmark, …) APPENDS the result to the END of that
    module's `db_migrate.apply(con, "<module>", [...])` statement list, so each
    ALTER runs exactly ONCE per database (db_migrate is versioned). Using one
    helper guarantees an IDENTICAL column definition everywhere.

    The column is `TEXT NOT NULL DEFAULT '<DEFAULT_TENANT_ID>'`:
      - TEXT is audit-safe (the json_object audit triggers reject BLOB, not TEXT).
      - the DEFAULT backfills EXISTING rows when the ALTER runs and stamps NEW
        rows that don't name the column — so nothing has to know about it yet.
    NOTHING SELECTs/filters this column in P1, so adding it changes no behavior;
    P2 wires `scope_clause()` into queries table-by-table behind the switch.
    """
    return [
        f"ALTER TABLE {t} ADD COLUMN {TENANT_COLUMN} TEXT NOT NULL "
        f"DEFAULT '{DEFAULT_TENANT_ID}'"
        for t in tables
    ]

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
    """Bind subsequent work on the CURRENT thread to `tenant_id` (a CLIENT-tenant
    user). The app's before-request hook calls this (only when multitenant is ON);
    call reset_tenant() when the unit of work finishes. A falsy value clears it.

    Setting a tenant CLEARS any owner scope on this thread — owner and tenant are
    mutually exclusive (a request is served as exactly one principal: a client
    tenant XOR the platform operator)."""
    _local.tenant = tenant_id or None
    _local.owner = False


def set_owner_scope():
    """Mark the CURRENT thread as the platform OPERATOR/owner (the business owner).

    This is the deliberate, audited cross-tenant exception: under owner scope the
    READ-path `scope_clause()` returns no filter, so owner analytics span EVERY
    tenant (the operator's "I must have all analytics" requirement). It is a
    READ-ONLY scope — see require_tenant(): a write still needs a concrete tenant.

    Owner and tenant are mutually exclusive: marking owner scope CLEARS any bound
    tenant on this thread."""
    _local.owner = True
    _local.tenant = None


def is_owner_scope():
    """True iff the current thread is in operator/owner scope. Never raises."""
    return bool(getattr(_local, "owner", False))


def current_tenant():
    """The tenant bound to the current thread, or None when unset. Never raises.

    NOTE: this returns whatever was set on THIS thread regardless of the global
    switch — but because the app's request hook only set_tenant()s while
    multitenant is ON, and the enforcement helpers below (`scope_clause`,
    `require_tenant`) are themselves switch-gated, an OFF install never has a
    tenant bound and is fully inert. Owner scope binds NO tenant, so this stays
    None under owner scope."""
    return getattr(_local, "tenant", None)


def reset_tenant():
    """Clear the current thread's context — both the bound tenant AND owner scope
    (after-request hook). Restores a fully unscoped thread."""
    _local.tenant = None
    _local.owner = False


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
    existing query in this slice.

    OWNER SCOPE IS READ-ONLY: this guard STILL raises when multitenant is ON and
    no concrete TENANT is set, EVEN under owner scope. The operator's cross-tenant
    privilege is for READ analytics only (see scope_clause); a write/mutation must
    always name a concrete tenant — the owner must not write tenant data without
    choosing whose data it is. So a write path under owner scope (no tenant bound)
    fails LOUD here, exactly as an unscoped client request would."""
    if not multitenant_enabled():
        return None
    t = current_tenant()
    if t is None:
        raise RuntimeError(
            "require_tenant(): multitenant is ON but no tenant is set on this "
            "request/thread — refusing to run a tenant-scoped operation unscoped. "
            "(Owner scope is read-only; a write must name a concrete tenant.)")
    return t


def write_tenant():
    """Return the tenant_id to STAMP on an INSERT — the WRITE-side mirror of
    scope_clause() (the read side). NEVER returns None.

    This is the reusable P2 write primitive: every INSERT/UPSERT that creates a
    tenant-owned row binds `tenant_id = tenancy.write_tenant()` in its explicit
    column list. Contract:

      - multitenant OFF -> DEFAULT_TENANT_ID ("default"). This is EXACTLY the
        column DEFAULT every tenant-scoped table was given in P1, so an OFF write
        that stamps it is byte-identical to one that omits the column entirely —
        OFF behavior is unchanged from today.
      - multitenant ON  -> require_tenant() (the concrete tenant bound to this
        request/thread). require_tenant() RAISES when ON and no concrete tenant is
        set — INCLUDING under owner scope, which is READ-ONLY (no tenant bound). So
        a write attempted with neither a tenant nor (intentionally) a writable
        owner context FAILS LOUD here rather than creating an unscoped/mis-scoped
        row. A write must always name whose data it is.

    Because require_tenant() is itself switch-gated (returns None while OFF), we
    only fall back to DEFAULT_TENANT_ID on the OFF path; ON always resolves to a
    non-empty concrete tenant (or raises). Never returns None either way."""
    t = require_tenant()
    return t if t is not None else DEFAULT_TENANT_ID


def queue_tenant():
    """Return the tenant_id to STAMP on a QUEUE / infrastructure write (an enqueued
    intake job) — the WRITE-side primitive for tenant-agnostic background work.
    NEVER raises (the distinction from write_tenant()).

    Contract:
      - multitenant OFF                 -> DEFAULT_TENANT_ID ("default"). Exactly the
        column DEFAULT every queue table got in P1, so an OFF enqueue that stamps it
        is byte-identical to one that omits the column — OFF behavior is unchanged.
      - multitenant ON + a tenant bound -> that tenant (a job parked by a tenant's web
        request captures that tenant, so the worker can later re-bind it).
      - multitenant ON + NO tenant bound -> DEFAULT_TENANT_ID. A job enqueued by the
        SCHEDULER/system (e.g. _scrape_tick, user='scheduler') has no tenant bound and
        correctly defaults to the implicit tenant.

    DISTINCTION FROM write_tenant(). write_tenant() is for user-facing CRM/product
    writes, where a tenant-less write under the switch is a BUG and must fail LOUD
    (it raises via require_tenant()). queue_tenant() is for infrastructure/queue
    writes that legitimately originate WITHOUT a bound tenant (the scheduler, system
    jobs); a missing tenant there is normal and defaults to DEFAULT_TENANT_ID rather
    than raising. Never returns None either way."""
    try:
        if multitenant_enabled():
            t = current_tenant()
            if t:
                return t
    except Exception as e:
        log.warning("queue_tenant read failed, defaulting to %s: %s",
                    DEFAULT_TENANT_ID, e)
    return DEFAULT_TENANT_ID


def owner_access_audit(resource):
    """Record that an OWNER cross-tenant access happened, so the deliberate
    exception is ACCOUNTABLE (actor + resource). Best-effort and NEVER raises —
    it is on the read path. Exposed for the P2 per-table work to call at each
    place owner scope widens a query beyond a single tenant.

    The access is logged to applog (logs/app.log) and, best-effort, to the audit
    trail attributed to the current thread's actor."""
    try:
        actor = "system"
        try:
            import audit
            actor = audit._current_actor()
        except Exception:
            actor = "system"
        log.info("OWNER cross-tenant access: actor=%s resource=%s", actor, resource)
        try:
            import audit
            con = connect()
            try:
                audit.record_event(con, "tenancy", "owner_scope", "owner_access",
                                   {"resource": str(resource), "actor": actor})
            finally:
                con.close()
        except Exception as e:
            log.debug("owner_access_audit: audit trail write skipped: %s", e)
    except Exception as e:
        log.warning("owner_access_audit(%r) failed, ignoring: %s", resource, e)


def scope_clause(column="tenant_id"):
    """Return an (sql_fragment, params) pair to AND into a tenant-scoped query.

    P2 (docs/MULTI_TENANCY.md) splices this into each tenant-scoped
    SELECT/UPDATE/DELETE, table-by-table, EACH with a cross-tenant access test.
    Applied so far: customer_master (CRM), pricing_intelligence (benchmark),
    supplier_master + invoice_control (suppliers.db), and the worker tenant
    context. Remaining modules follow the same pattern.

    Contract:
      - multitenant OFF            -> ("", [])    a literal no-op: existing SQL is
                                                  unchanged, so OFF installs behave
                                                  byte-identically to today.
      - ON + OWNER scope           -> ("", [])    the deliberate, AUDITED owner
                                                  cross-tenant exception (see below).
      - ON + a tenant set          -> (" AND {col} = ?", [current_tenant()])
      - ON + NEITHER owner nor tenant -> (" AND 1=0", [])  FAIL CLOSED: matches
                                                  nothing, so a missing context can
                                                  never leak another tenant's rows.

    THE OWNER EXCEPTION. When multitenant is ON and the request is in operator/
    owner scope (`is_owner_scope()`), scope_clause returns the SAME no-op as OFF —
    no tenant filter — so the platform OPERATOR sees ALL tenants. This is the ONE
    place scope_clause deliberately returns no filter while the switch is ON; it
    encodes the owner's "I must have all analytics data" requirement. Per
    docs/SECURITY_COMPLIANCE_PLAN.md §7, owner cross-tenant analytics MUST run on
    DE-IDENTIFIED / AGGREGATED data with PII excluded (IBANs, driver/vehicle,
    contacts) — never relay one client's identifiable current pricing to another
    (antitrust). The widening is accountable via owner_access_audit().

    FAIL CLOSED on a missing context. When the switch is ON but NEITHER an owner
    scope NOR a tenant is bound, we return a matches-NOTHING clause (" AND 1=0")
    rather than the no-op — a request that forgot to resolve its principal must
    NOT accidentally read across tenants. This is the safe default for the
    enforcement primitive; OFF-by-default keeps it fully inert (this branch is
    only reachable with the switch ON).

    The fragment leads with " AND " so it appends after an existing WHERE; the
    column name is interpolated (caller-controlled identifier, never user input)
    and the tenant VALUE is always a bound parameter. Never raises."""
    try:
        if not multitenant_enabled():
            return ("", [])
        # The deliberate, audited owner cross-tenant exception: operator sees all.
        if is_owner_scope():
            return ("", [])
        t = current_tenant()
        if t is None:
            # Fail CLOSED: a missing tenant context must match nothing, never leak.
            log.warning("scope_clause(%r): multitenant ON but neither owner nor "
                        "tenant set — failing closed (matches nothing).", column)
            return (" AND 1=0", [])
        return (f" AND {column} = ?", [t])
    except Exception as e:
        # A failure here must never WIDEN a scope. Fail CLOSED (match nothing) unless
        # multitenant is provably OFF (then the inert no-op is the correct behavior).
        # If we cannot even determine the switch state, assume ON and fail closed.
        log.warning("scope_clause(%r) failed: %s", column, e)
        try:
            off = not multitenant_enabled()
        except Exception:
            off = False
        return ("", []) if off else (" AND 1=0", [])


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
