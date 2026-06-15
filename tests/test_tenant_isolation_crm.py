"""Multi-tenancy P2 — CROSS-TENANT ISOLATION harness (CRM proof-of-pattern).

This is the TEMPLATE every P2 module's isolation test copies. It proves the two
halves of enforcement on `customer_master.py`, behind the `multitenant` switch:

  * WRITES stamp the bound tenant (`tenancy.write_tenant()`), so a row created
    "as tenant A" carries tenant_id='A'.
  * READS filter by `tenancy.scope_clause()`, so tenant A NEVER sees tenant B's
    rows (the core GDPR-isolation proof), the platform OWNER sees BOTH (the
    deliberate audited cross-tenant analytics exception), and a tenant-less write
    FAILS LOUD rather than creating an unscoped row.

CARDINAL invariant also asserted here: with the switch OFF (the default) the
write stamps 'default' and reads are unscoped — byte-identical to today. The
existing CRM suite (test_customers.py) is the standing proof OFF is unchanged;
this file adds one explicit OFF assertion alongside the ON isolation proofs.
"""
import importlib

import pytest


@pytest.fixture()
def crm(tmp_path, monkeypatch):
    """A fresh CRM (customers.db) + security.db with the `multitenant` switch ON.

    Points both module DBs at tmp files, clears every per-process schema cache so
    the schemas are (re)created against the tmp files, and arms the switch. Yields
    (customer_master, tenancy) plus turns the thread context inert on teardown.
    """
    import auth
    import tenancy
    import customer_master
    importlib.reload(customer_master)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "cust.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "docs"))

    # Arm the master switch (stored in app_settings/security.db like every module
    # switch). This is what makes scope_clause()/write_tenant() engage.
    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True

    try:
        yield customer_master, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_two_tenants(cm, tenancy):
    """As tenant A create ACME; as tenant B create BETA — both via the REAL
    add_customer write path, proving the INSERT stamps the bound tenant."""
    tenancy.set_tenant("A")
    cm.add_customer("ACME", "Acme SIA", country="LV")
    tenancy.set_tenant("B")
    cm.add_customer("BETA", "Beta UAB", country="LT")
    tenancy.reset_tenant()


# ── WRITE stamping ──────────────────────────────────────────────────────────────

def test_add_customer_stamps_the_bound_tenant(crm):
    cm, tenancy = crm
    _seed_two_tenants(cm, tenancy)
    # Inspect the raw rows with NO scope (owner) to read the stamped tenant_id.
    tenancy.set_owner_scope()
    con = cm.connect()
    try:
        rows = {r["code"]: r["tenant_id"]
                for r in con.execute("SELECT code, tenant_id FROM customers")}
    finally:
        con.close()
    assert rows == {"ACME": "A", "BETA": "B"}


# ── READ isolation (the core GDPR proof) ────────────────────────────────────────

def test_tenant_a_sees_only_acme(crm):
    cm, tenancy = crm
    _seed_two_tenants(cm, tenancy)
    tenancy.set_tenant("A")
    assert cm.get_customer("ACME")["company_name"] == "Acme SIA"
    # BETA is invisible — get_customer's miss path returns the INPUT stub, not Beta.
    miss = cm.get_customer("BETA")
    assert miss["company_name"] == "BETA"
    assert miss["reg_number"] == "INPUT"
    con = cm.connect()
    try:
        codes = {r["code"] for r in cm.list_customers(con)}
    finally:
        con.close()
    assert codes == {"ACME"}


def test_tenant_b_sees_only_beta(crm):
    cm, tenancy = crm
    _seed_two_tenants(cm, tenancy)
    tenancy.set_tenant("B")
    assert cm.get_customer("BETA")["company_name"] == "Beta UAB"
    miss = cm.get_customer("ACME")
    assert miss["company_name"] == "ACME"
    assert miss["reg_number"] == "INPUT"
    con = cm.connect()
    try:
        codes = {r["code"] for r in cm.list_customers(con)}
    finally:
        con.close()
    assert codes == {"BETA"}


def test_is_active_is_tenant_scoped(crm):
    cm, tenancy = crm
    _seed_two_tenants(cm, tenancy)
    tenancy.set_tenant("A")
    # ACME exists for A (pending); BETA is invisible -> is_active returns None.
    assert cm.is_active("ACME") is False
    assert cm.is_active("BETA") is None


# ── OWNER cross-tenant scope (the audited analytics exception) ───────────────────

def test_owner_scope_sees_both_tenants(crm):
    cm, tenancy = crm
    _seed_two_tenants(cm, tenancy)
    tenancy.set_owner_scope()
    con = cm.connect()
    try:
        codes = {r["code"] for r in cm.list_customers(con)}
    finally:
        con.close()
    assert codes == {"ACME", "BETA"}


# ── WRITE GUARD: a tenant-less write must FAIL LOUD ─────────────────────────────

def test_write_without_tenant_or_owner_raises(crm):
    cm, tenancy = crm
    # Switch ON, neither tenant nor owner bound -> write_tenant()/require_tenant raise.
    tenancy.reset_tenant()
    with pytest.raises(RuntimeError):
        cm.add_customer("NOPE", "No Tenant Co")


def test_write_under_owner_scope_raises(crm):
    cm, tenancy = crm
    # Owner scope is READ-ONLY: a write must name a concrete tenant.
    tenancy.set_owner_scope()
    with pytest.raises(RuntimeError):
        cm.add_customer("NOPE", "Owner Cannot Write Co")


# ── OFF regression: byte-identical to today ─────────────────────────────────────

def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    """With the switch OFF (the default), writes stamp 'default' (== the column
    DEFAULT) and reads are unscoped — identical to today."""
    import auth
    import tenancy
    import customer_master
    importlib.reload(customer_master)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "cust.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "docs"))
    # Setting absent/0 = OFF (default).
    assert tenancy.multitenant_enabled() is False

    # Even with a tenant set on the thread, OFF keeps scope_clause/write_tenant inert.
    tenancy.set_tenant("A")
    customer_master.add_customer("ACME", "Acme SIA", country="LV")
    tenancy.reset_tenant()

    con = customer_master.connect()
    try:
        row = con.execute(
            "SELECT tenant_id FROM customers WHERE code='ACME'").fetchone()
        assert row["tenant_id"] == "default"   # stamped the column DEFAULT
        # Reads are unscoped: visible regardless of any thread tenant.
        tenancy.set_tenant("ZZZ")
        assert customer_master.get_customer("ACME")["company_name"] == "Acme SIA"
        assert {r["code"] for r in customer_master.list_customers(con)} == {"ACME"}
    finally:
        con.close()
        tenancy.reset_tenant()


def test_delete_template_is_tenant_scoped(crm):
    """A tenant cannot delete another tenant's doc template by id (the write-scope
    invariant: every tenant-owned write is scoped). Inert when the switch is OFF."""
    cm, tenancy = crm
    tenancy.set_tenant("A")          # bind before connect (connect may re-seed under ON)
    con = cm.connect()
    try:
        tid = cm.add_template(con, "A's PoA", "poa", "poa.txt", b"hello")
        tenancy.set_tenant("B")
        cm.delete_template(con, tid)                 # B must NOT delete A's template
        tenancy.set_tenant("A")
        assert cm.get_template(con, tid) is not None  # survived B's delete
        cm.delete_template(con, tid)                  # A can delete its own
        assert cm.get_template(con, tid) is None
    finally:
        con.close()
        tenancy.reset_tenant()
