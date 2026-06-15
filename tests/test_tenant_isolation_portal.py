"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for portal_scraper.py (credential custody).

Mirrors tests/test_tenant_isolation_crm.py (the P2 template) for the USER-FACING
portal credential/config CRUD. This is the SECURITY-CRITICAL slice: under the SaaS
model one tenant MUST NOT read or overwrite another tenant's stored portal
credentials/config (a GDPR isolation guarantee). Proves, behind the `multitenant`
switch:

  * set_config() / set_credentials() (USER-FACING admin writes) stamp the bound
    tenant via tenancy.write_tenant();
  * get_config() / get_credentials() / list_configs() / list_portals() filter by
    tenancy.scope_clause() — tenant A NEVER sees tenant B's config/credentials/runs;
    the platform OWNER sees BOTH (the audited cross-tenant exception);
  * delete_credentials() is tenant-scoped — B cannot delete A's credential;
  * a tenant-less user-facing write under the switch FAILS LOUD (write_tenant ->
    require_tenant raises);
  * with the switch OFF (default) writes stamp 'default' and reads are unscoped —
    byte-identical to today (the regression proof).

NOTE on the known ON CONFLICT limitation (flagged in portal_scraper.py, NOT fixed in
this slice): set_config's ON CONFLICT(supplier) and set_credentials' ON CONFLICT
(supplier, entity) targets are not tenant-qualified, so two tenants using the SAME
supplier/(supplier, entity) would collide under the switch ON. These tests sidestep
that by giving each tenant a DISTINCT supplier/entity (PORTA/PORTB, ENTA/ENTB).

Credentials are envelope-encrypted via keyvault (local KEK derived from the app
secret key) — the existing portal_scraper test fixture proves the round-trip works
with no extra setup, so we mirror it.
"""
import importlib

import pytest


@pytest.fixture()
def ps(tmp_path, monkeypatch):
    """A fresh portal.db + security.db (tenant registry) with `multitenant` ON.

    Points portal_scraper.DB and auth.DB (the tenancy registry home) at tmp files,
    clears every per-process schema cache, isolates the MY-Prices store, and arms the
    switch. Yields (portal_scraper, tenancy) and turns the thread context inert on
    teardown.
    """
    import auth
    import tenancy
    import portal_scraper
    importlib.reload(portal_scraper)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(portal_scraper, "DB", str(tmp_path / "portal.db"))
    portal_scraper._SCHEMA_READY.clear()
    # isolate the MY-Prices store the scraper would load into (not exercised here)
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    monkeypatch.setattr(pricing_intelligence, "DB", str(tmp_path / "fuel_history.db"))
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True

    try:
        yield portal_scraper, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_two_tenants(ps, tenancy):
    """As tenant A configure PORTA + store a credential; as tenant B configure PORTB.
    DISTINCT supplier/entity per tenant sidesteps the (un-tenant-qualified) ON CONFLICT
    limitation that is flagged-not-fixed in this slice."""
    tenancy.set_tenant("A")
    ps.set_config("PORTA", "demo")
    ps.set_credentials("PORTA", "ENTA", "user-a", "secret-a")
    tenancy.set_tenant("B")
    ps.set_config("PORTB", "demo")
    ps.set_credentials("PORTB", "ENTB", "user-b", "secret-b")
    tenancy.reset_tenant()


# ── WRITE stamping ──────────────────────────────────────────────────────────────

def test_writes_stamp_the_bound_tenant(ps):
    ps_mod, tenancy = ps
    _seed_two_tenants(ps_mod, tenancy)
    # Inspect raw rows with NO scope (owner) to read the stamped tenant_id.
    tenancy.set_owner_scope()
    con = ps_mod.connect()
    try:
        cfgs = {r["supplier"]: r["tenant_id"]
                for r in con.execute("SELECT supplier, tenant_id FROM portal_configs")}
        creds = {(r["supplier"], r["entity"]): r["tenant_id"] for r in
                 con.execute("SELECT supplier, entity, tenant_id FROM portal_credentials")}
    finally:
        con.close()
    assert cfgs == {"PORTA": "A", "PORTB": "B"}
    assert creds == {("PORTA", "ENTA"): "A", ("PORTB", "ENTB"): "B"}


# ── READ isolation (the core GDPR custody proof) ────────────────────────────────

def test_tenant_a_sees_only_its_own(ps):
    ps_mod, tenancy = ps
    _seed_two_tenants(ps_mod, tenancy)
    tenancy.set_tenant("A")
    # config
    assert ps_mod.get_config("PORTA")["supplier"] == "PORTA"
    assert ps_mod.get_config("PORTB") is None          # B's config invisible
    assert {c["supplier"] for c in ps_mod.list_configs()} == {"PORTA"}
    # credentials (the custody-isolation guarantee)
    assert ps_mod.get_credentials("PORTA", "ENTA")["secret"] == "secret-a"
    assert ps_mod.get_credentials("PORTB", "ENTB") is None
    # list_portals scopes both the credential listing and the run history
    assert {p["supplier"] for p in ps_mod.list_portals()} == {"PORTA"}


def test_tenant_b_sees_only_its_own(ps):
    ps_mod, tenancy = ps
    _seed_two_tenants(ps_mod, tenancy)
    tenancy.set_tenant("B")
    assert ps_mod.get_config("PORTB")["supplier"] == "PORTB"
    assert ps_mod.get_config("PORTA") is None
    assert {c["supplier"] for c in ps_mod.list_configs()} == {"PORTB"}
    assert ps_mod.get_credentials("PORTB", "ENTB")["secret"] == "secret-b"
    assert ps_mod.get_credentials("PORTA", "ENTA") is None
    assert {p["supplier"] for p in ps_mod.list_portals()} == {"PORTB"}


# ── OWNER cross-tenant scope (the audited analytics exception) ───────────────────

def test_owner_scope_sees_both_tenants(ps):
    ps_mod, tenancy = ps
    _seed_two_tenants(ps_mod, tenancy)
    tenancy.set_owner_scope()
    assert {c["supplier"] for c in ps_mod.list_configs()} == {"PORTA", "PORTB"}
    assert {p["supplier"] for p in ps_mod.list_portals()} == {"PORTA", "PORTB"}
    assert ps_mod.get_config("PORTA")["supplier"] == "PORTA"
    assert ps_mod.get_config("PORTB")["supplier"] == "PORTB"


# ── DELETE isolation: B cannot delete A's credential ────────────────────────────

def test_delete_credentials_is_tenant_scoped(ps):
    ps_mod, tenancy = ps
    _seed_two_tenants(ps_mod, tenancy)
    # B tries to delete A's credential -> must NOT remove it.
    tenancy.set_tenant("B")
    ps_mod.delete_credentials("PORTA", "ENTA")
    tenancy.set_tenant("A")
    assert ps_mod.get_credentials("PORTA", "ENTA") is not None   # survived B's delete
    # A can delete its own.
    ps_mod.delete_credentials("PORTA", "ENTA")
    assert ps_mod.get_credentials("PORTA", "ENTA") is None


# ── WRITE GUARD: a tenant-less user-facing write must FAIL LOUD ──────────────────

def test_set_config_without_tenant_or_owner_raises(ps):
    ps_mod, tenancy = ps
    tenancy.reset_tenant()      # switch ON, neither tenant nor owner bound
    with pytest.raises(RuntimeError):
        ps_mod.set_config("NOPE", "demo")


def test_set_credentials_without_tenant_or_owner_raises(ps):
    ps_mod, tenancy = ps
    tenancy.reset_tenant()
    with pytest.raises(RuntimeError):
        ps_mod.set_credentials("NOPE", "X", "u", "s")


def test_write_under_owner_scope_raises(ps):
    ps_mod, tenancy = ps
    # Owner scope is READ-ONLY: a write must name a concrete tenant.
    tenancy.set_owner_scope()
    with pytest.raises(RuntimeError):
        ps_mod.set_credentials("NOPE", "X", "u", "s")


# ── OFF regression: byte-identical to today ─────────────────────────────────────

def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    """With the switch OFF (the default), writes stamp 'default' (== the column
    DEFAULT) and reads are unscoped — identical to today."""
    import auth
    import tenancy
    import portal_scraper
    importlib.reload(portal_scraper)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(portal_scraper, "DB", str(tmp_path / "portal.db"))
    portal_scraper._SCHEMA_READY.clear()
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    monkeypatch.setattr(pricing_intelligence, "DB", str(tmp_path / "fuel_history.db"))
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))
    # Setting absent/0 = OFF (default).
    assert tenancy.multitenant_enabled() is False

    # Even with a tenant set on the thread, OFF keeps scope_clause/write_tenant inert.
    tenancy.set_tenant("A")
    portal_scraper.set_config("PORTA", "demo")
    portal_scraper.set_credentials("PORTA", "ENTA", "user-a", "secret-a")
    tenancy.reset_tenant()

    con = portal_scraper.connect()
    try:
        cfg_t = con.execute(
            "SELECT tenant_id FROM portal_configs WHERE supplier='PORTA'").fetchone()
        cred_t = con.execute(
            "SELECT tenant_id FROM portal_credentials WHERE supplier='PORTA'").fetchone()
        assert cfg_t["tenant_id"] == "default"    # stamped the column DEFAULT
        assert cred_t["tenant_id"] == "default"
    finally:
        con.close()

    # Reads are unscoped: visible regardless of any thread tenant.
    tenancy.set_tenant("ZZZ")
    try:
        assert portal_scraper.get_config("PORTA")["supplier"] == "PORTA"
        assert portal_scraper.get_credentials("PORTA", "ENTA")["secret"] == "secret-a"
        assert {c["supplier"] for c in portal_scraper.list_configs()} == {"PORTA"}
        assert {p["supplier"] for p in portal_scraper.list_portals()} == {"PORTA"}
    finally:
        tenancy.reset_tenant()
