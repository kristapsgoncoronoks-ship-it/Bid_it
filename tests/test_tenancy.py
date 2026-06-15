"""
Tests for the multi-tenancy FOUNDATION (P0): tenancy.py.

The whole point of this slice is ZERO behavior change for the default
single-tenant install. So the load-bearing assertions here are the
OFF-by-default INERTNESS ones: with `multitenant` OFF, scope_clause() is a
literal no-op and require_tenant() does not raise — proving no existing query
or figure is touched.

The registry lives in security.db, which the session-scoped admin fixture backs
up and restores; we additionally save/restore the `multitenant` setting around
the ON-path tests so we never leak the switch into the rest of the suite.
"""
import os
import threading

import pytest

import auth
import tenancy


# ── helpers ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def _clean_context():
    """Guarantee the thread-local tenant is clear before AND after each test, so
    ordering never leaks a bound tenant between tests."""
    tenancy.reset_tenant()
    yield
    tenancy.reset_tenant()


@pytest.fixture()
def multitenant_on(admin_session, _clean_context):
    """Turn the `multitenant` switch ON for the duration of a test, restoring the
    prior value (default OFF) afterwards. Depends on admin_session so security.db
    is the throwaway test DB."""
    prev = auth.get_setting(tenancy.SETTING, None)
    auth.set_setting(tenancy.SETTING, "1")
    assert tenancy.multitenant_enabled() is True
    try:
        yield
    finally:
        if prev is None:
            auth.set_setting(tenancy.SETTING, "0")
        else:
            auth.set_setting(tenancy.SETTING, prev)


# ── registry CRUD ──────────────────────────────────────────────────────────────

def test_registry_crud_roundtrip(admin_session):
    tenancy.create_tenant("acme", "Acme Transport")
    t = tenancy.get_tenant("acme")
    assert t is not None
    assert t["tenant_id"] == "acme"
    assert t["name"] == "Acme Transport"
    assert t["active"] == 1
    assert t["created_at"]

    ids = {r["tenant_id"] for r in tenancy.list_tenants()}
    assert "acme" in ids


def test_create_tenant_is_idempotent_and_reactivates(admin_session):
    tenancy.create_tenant("baltic", "Baltic Co")
    tenancy.set_active("baltic", 0)
    assert tenancy.get_tenant("baltic")["active"] == 0
    # re-create updates the name and re-activates
    t = tenancy.create_tenant("baltic", "Baltic Logistics")
    assert t["name"] == "Baltic Logistics"
    assert t["active"] == 1


def test_set_active_toggles(admin_session):
    tenancy.create_tenant("nordic", "Nordic")
    assert tenancy.set_active("nordic", 0)["active"] == 0
    assert tenancy.set_active("nordic", 1)["active"] == 1


def test_get_tenant_unknown_is_none(admin_session):
    assert tenancy.get_tenant("does-not-exist") is None
    assert tenancy.get_tenant("") is None
    assert tenancy.get_tenant(None) is None


def test_create_tenant_rejects_empty_id(admin_session):
    with pytest.raises(ValueError):
        tenancy.create_tenant("", "no id")
    with pytest.raises(ValueError):
        tenancy.create_tenant("   ", "blank")


# ── thread-local context ───────────────────────────────────────────────────────

def test_context_set_get_reset(_clean_context):
    assert tenancy.current_tenant() is None
    tenancy.set_tenant("acme")
    assert tenancy.current_tenant() == "acme"
    tenancy.reset_tenant()
    assert tenancy.current_tenant() is None


def test_context_is_thread_local(_clean_context):
    """A tenant set on this thread must NOT bleed into another thread — the same
    isolation property audit.py relies on for the actor."""
    tenancy.set_tenant("main-thread")
    seen = {}

    def worker():
        # fresh thread => no inherited tenant
        seen["before"] = tenancy.current_tenant()
        tenancy.set_tenant("worker-thread")
        seen["after"] = tenancy.current_tenant()

    th = threading.Thread(target=worker)
    th.start()
    th.join()

    assert seen["before"] is None          # no bleed INTO the worker
    assert seen["after"] == "worker-thread"
    assert tenancy.current_tenant() == "main-thread"   # no bleed BACK


# ── owner/operator scope: mutual exclusivity & reset (switch-independent) ────────

def test_owner_and_tenant_are_mutually_exclusive(_clean_context):
    """Setting owner scope clears any bound tenant, and binding a tenant clears
    owner scope — a thread is exactly one principal at a time."""
    assert tenancy.is_owner_scope() is False
    assert tenancy.current_tenant() is None

    tenancy.set_owner_scope()
    assert tenancy.is_owner_scope() is True
    assert tenancy.current_tenant() is None      # owner binds NO tenant

    tenancy.set_tenant("acme")                   # binding a tenant clears owner
    assert tenancy.is_owner_scope() is False
    assert tenancy.current_tenant() == "acme"

    tenancy.set_owner_scope()                     # and back: clears the tenant
    assert tenancy.is_owner_scope() is True
    assert tenancy.current_tenant() is None


def test_reset_clears_owner_scope(_clean_context):
    tenancy.set_owner_scope()
    assert tenancy.is_owner_scope() is True
    tenancy.reset_tenant()
    assert tenancy.is_owner_scope() is False
    assert tenancy.current_tenant() is None


def test_owner_scope_is_thread_local(_clean_context):
    """Owner scope set on this thread must NOT bleed into another thread."""
    tenancy.set_owner_scope()
    seen = {}

    def worker():
        seen["before"] = tenancy.is_owner_scope()    # fresh thread => not owner
        tenancy.set_owner_scope()
        seen["after"] = tenancy.is_owner_scope()

    th = threading.Thread(target=worker)
    th.start()
    th.join()

    assert seen["before"] is False               # no bleed INTO the worker
    assert seen["after"] is True
    assert tenancy.is_owner_scope() is True       # no bleed BACK


# ── OFF-by-default inertness (the load-bearing proof of zero behavior change) ────

def test_off_by_default(admin_session, _clean_context):
    # The default install has the switch OFF.
    prev = auth.get_setting(tenancy.SETTING, None)
    if prev is not None:
        auth.set_setting(tenancy.SETTING, "0")
    try:
        assert tenancy.multitenant_enabled() is False
        # scope_clause is a literal no-op: existing SQL is unchanged.
        assert tenancy.scope_clause() == ("", [])
        assert tenancy.scope_clause("customer_id") == ("", [])
        # require_tenant does NOT raise while OFF, even with no tenant bound.
        assert tenancy.require_tenant() is None
    finally:
        if prev is not None:
            auth.set_setting(tenancy.SETTING, prev)


def test_off_owner_scope_is_inert(admin_session, _clean_context):
    """Even in owner scope, while the switch is OFF scope_clause stays the no-op —
    the switch, not the context, gates enforcement. ZERO behavior change."""
    prev = auth.get_setting(tenancy.SETTING, None)
    if prev is not None:
        auth.set_setting(tenancy.SETTING, "0")
    try:
        tenancy.set_owner_scope()
        assert tenancy.is_owner_scope() is True
        assert tenancy.multitenant_enabled() is False
        assert tenancy.scope_clause() == ("", [])
        assert tenancy.scope_clause("customer_id") == ("", [])
        # require_tenant does NOT raise while OFF, even under owner scope.
        assert tenancy.require_tenant() is None
    finally:
        if prev is not None:
            auth.set_setting(tenancy.SETTING, prev)


def test_off_scope_clause_unaffected_by_bound_tenant(admin_session, _clean_context):
    """Even if some thread bound a tenant, while the switch is OFF scope_clause
    stays the no-op — the switch, not the context, gates enforcement."""
    prev = auth.get_setting(tenancy.SETTING, None)
    if prev is not None:
        auth.set_setting(tenancy.SETTING, "0")
    try:
        tenancy.set_tenant("acme")
        assert tenancy.multitenant_enabled() is False
        assert tenancy.scope_clause() == ("", [])
        assert tenancy.require_tenant() is None
    finally:
        if prev is not None:
            auth.set_setting(tenancy.SETTING, prev)


# ── ON behavior (enforcement primitives arm) ────────────────────────────────────

def test_on_scope_clause_filters(multitenant_on):
    tenancy.set_tenant("acme")
    frag, params = tenancy.scope_clause()
    assert frag == " AND tenant_id = ?"
    assert params == ["acme"]
    # custom column name flows through, value stays parameterized
    frag2, params2 = tenancy.scope_clause("owner_tenant")
    assert frag2 == " AND owner_tenant = ?"
    assert params2 == ["acme"]


def test_on_require_tenant_raises_when_unset(multitenant_on):
    tenancy.reset_tenant()
    with pytest.raises(RuntimeError):
        tenancy.require_tenant()


def test_on_require_tenant_returns_bound(multitenant_on):
    tenancy.set_tenant("baltic")
    assert tenancy.require_tenant() == "baltic"


# ── ON behavior: owner scope (the audited cross-tenant exception) ────────────────

def test_on_owner_scope_sees_all(multitenant_on):
    """Under owner scope with the switch ON, scope_clause returns NO filter — the
    deliberate cross-tenant analytics exception (operator sees all tenants)."""
    tenancy.set_owner_scope()
    assert tenancy.is_owner_scope() is True
    assert tenancy.scope_clause() == ("", [])
    assert tenancy.scope_clause("customer_id") == ("", [])


def test_scope_clause_fails_closed_on_internal_error_when_on(multitenant_on, monkeypatch):
    """If scope_clause's body raises while multitenant is ON, it must fail CLOSED
    (matches nothing), never widen to the no-op."""
    def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(tenancy, "is_owner_scope", boom)
    frag, params = tenancy.scope_clause()
    assert frag == " AND 1=0" and params == []


def test_scope_clause_internal_error_when_off_is_inert(monkeypatch):
    """The same class of failure while multitenant is OFF degrades to the inert
    no-op (single-tenant correctness)."""
    monkeypatch.setattr(tenancy, "multitenant_enabled", lambda: False)
    frag, params = tenancy.scope_clause()
    assert frag == "" and params == []


def test_on_neither_set_fails_closed(multitenant_on):
    """Switch ON but NEITHER owner nor tenant resolved -> fail CLOSED: a
    matches-nothing clause so a missing context can never leak cross-tenant."""
    tenancy.reset_tenant()
    assert tenancy.is_owner_scope() is False
    assert tenancy.current_tenant() is None
    frag, params = tenancy.scope_clause()
    assert frag == " AND 1=0"
    assert params == []

    # Prove it matches nothing against a real table.
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE thing (id INTEGER, tenant_id TEXT)")
    con.executemany("INSERT INTO thing VALUES (?,?)", [(1, "alpha"), (2, "beta")])
    con.commit()
    rows = con.execute(f"SELECT id FROM thing WHERE 1=1{frag}", params).fetchall()
    assert rows == []
    con.close()


def test_on_require_tenant_raises_under_owner_scope(multitenant_on):
    """Owner scope is READ-only: require_tenant() still raises with no concrete
    tenant bound, so the owner can't WRITE tenant data without naming a tenant."""
    tenancy.set_owner_scope()
    assert tenancy.is_owner_scope() is True
    with pytest.raises(RuntimeError):
        tenancy.require_tenant()


def test_owner_access_audit_never_raises(admin_session, _clean_context):
    """owner_access_audit is on the read path — best-effort, never raises."""
    # Normal call.
    tenancy.owner_access_audit("recovery_report")
    # Even with a broken audit/connect underneath, it must swallow and log.
    import audit
    orig = tenancy.connect

    def boom():
        raise RuntimeError("registry down")

    try:
        tenancy.connect = boom
        tenancy.owner_access_audit("anything")   # must not raise
    finally:
        tenancy.connect = orig


# ── never-raise on a broken/missing registry ────────────────────────────────────

def test_read_path_never_raises_on_broken_registry(monkeypatch):
    """get_tenant/list_tenants/multitenant_enabled must degrade, not raise, if the
    registry connect() blows up (e.g. disk gone). They are on the request read
    path."""
    def boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(tenancy, "connect", boom)
    monkeypatch.setattr(auth, "get_setting", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no settings")))

    assert tenancy.get_tenant("acme") is None
    assert tenancy.list_tenants() == []
    # multitenant_enabled degrades to OFF (the safe single-tenant default)
    assert tenancy.multitenant_enabled() is False
    # and with the switch read failing -> OFF -> scope_clause is the no-op
    assert tenancy.scope_clause() == ("", [])


# ── cross-tenant harness DEMO (template for the P2 per-table tests) ──────────────

def test_cross_tenant_scope_demo(multitenant_on, _clean_context):
    """Seed two tenants, bind context to tenant A, and show scope_clause would
    filter to A only. This is the SHAPE every P2 per-table test takes: bind a
    tenant, splice scope_clause into the query, assert no rows from the OTHER
    tenant come back. It modifies NO existing product query."""
    tenancy.create_tenant("alpha", "Alpha")
    tenancy.create_tenant("beta", "Beta")

    # Simulate a tenant-scoped product table in a throwaway in-memory DB.
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE thing (id INTEGER, tenant_id TEXT, payload TEXT)")
    con.executemany("INSERT INTO thing VALUES (?,?,?)",
                    [(1, "alpha", "a1"), (2, "alpha", "a2"), (3, "beta", "b1")])
    con.commit()

    tenancy.set_tenant("alpha")
    frag, params = tenancy.scope_clause()
    rows = con.execute(f"SELECT payload FROM thing WHERE 1=1{frag}", params).fetchall()
    payloads = {r[0] for r in rows}
    assert payloads == {"a1", "a2"}        # tenant A's rows only
    assert "b1" not in payloads            # NO bleed from tenant B
    con.close()


# ── web regression: app renders normally with multitenant OFF ───────────────────

def test_web_renders_with_multitenant_off(client):
    """The dashboard renders normally with the switch OFF — proving the request
    hook is inert (it never binds a tenant on the OFF path)."""
    r = client.get("/")
    assert r.status_code == 200


def test_admin_tenants_is_admin_only_and_renders(client):
    """The read-only /admin/tenants surface is reachable by the admin fixture and
    shows the single-tenant mode banner when OFF."""
    r = client.get("/admin/tenants")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Multi-tenancy registry" in body
    # OFF by default -> single-tenant mode is stated, nothing is gated.
    assert "single-tenant mode" in body
