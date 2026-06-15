"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for finance.py (advances ledger).

Mirrors tests/test_tenant_isolation_crm.py (the P2 template) for the finance-owned
advances ledger. Proves, behind the `multitenant` switch:

  * request_advance() (a USER-FACING origination write from the Receivables financing
    page) stamps the bound tenant via tenancy.write_tenant();
  * list_advances() filters by tenancy.scope_clause() — tenant A never sees tenant B's
    advances; the platform OWNER sees BOTH;
  * a tenant-less user-facing write under the switch FAILS LOUD (write_tenant ->
    require_tenant raises);
  * with the switch OFF (default) the write stamps 'default' and reads are unscoped —
    byte-identical to today.

The NullProvider is the default (no money moves); request_advance still records the
ledger INTENT row, which is what we isolate.
"""
import importlib

import pytest


@pytest.fixture()
def fin(tmp_path, monkeypatch):
    """A fresh finance.db + security.db with the `multitenant` switch ON."""
    import auth
    import tenancy
    import finance
    importlib.reload(finance)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(finance, "DB", str(tmp_path / "finance.db"))
    monkeypatch.setattr(finance, "_READY", set())

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True

    try:
        yield finance, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_two_tenants(finance, tenancy):
    """As tenant A request an advance on CLAIM-A; as tenant B on CLAIM-B — via the REAL
    request_advance write path, proving the INSERT stamps the bound tenant."""
    tenancy.set_tenant("A")
    finance.request_advance("CLAIM-A", 1000.0, 20.0, actor="alice")
    tenancy.set_tenant("B")
    finance.request_advance("CLAIM-B", 2000.0, 40.0, actor="bob")
    tenancy.reset_tenant()


# ── WRITE stamping ──────────────────────────────────────────────────────────────

def test_request_advance_stamps_the_bound_tenant(fin):
    finance, tenancy = fin
    _seed_two_tenants(finance, tenancy)
    tenancy.set_owner_scope()
    con = finance.connect()
    try:
        rows = {r["claim_key"]: r["tenant_id"]
                for r in con.execute("SELECT claim_key, tenant_id FROM advances")}
    finally:
        con.close()
    assert rows == {"CLAIM-A": "A", "CLAIM-B": "B"}


# ── READ isolation (the core GDPR proof) ────────────────────────────────────────

def test_tenant_a_sees_only_its_advances(fin):
    finance, tenancy = fin
    _seed_two_tenants(finance, tenancy)
    tenancy.set_tenant("A")
    keys = {r["claim_key"] for r in finance.list_advances()}
    assert keys == {"CLAIM-A"}


def test_tenant_b_sees_only_its_advances(fin):
    finance, tenancy = fin
    _seed_two_tenants(finance, tenancy)
    tenancy.set_tenant("B")
    keys = {r["claim_key"] for r in finance.list_advances()}
    assert keys == {"CLAIM-B"}


# ── OWNER cross-tenant scope (the audited analytics exception) ───────────────────

def test_owner_scope_sees_both_tenants(fin):
    finance, tenancy = fin
    _seed_two_tenants(finance, tenancy)
    tenancy.set_owner_scope()
    keys = {r["claim_key"] for r in finance.list_advances()}
    assert keys == {"CLAIM-A", "CLAIM-B"}


# ── WRITE GUARD: a tenant-less user-facing write must FAIL LOUD ──────────────────

def test_request_advance_without_tenant_raises(fin):
    finance, tenancy = fin
    # Switch ON, neither tenant nor owner bound -> write_tenant()/require_tenant raise
    # INSIDE the INSERT. request_advance must propagate (not swallow) the loud failure.
    tenancy.reset_tenant()
    with pytest.raises(RuntimeError):
        finance.request_advance("NOPE", 100.0, 2.0, actor="nobody")


def test_request_advance_under_owner_scope_raises(fin):
    finance, tenancy = fin
    # Owner scope is READ-ONLY: a write must name a concrete tenant.
    tenancy.set_owner_scope()
    with pytest.raises(RuntimeError):
        finance.request_advance("NOPE", 100.0, 2.0, actor="owner")


# ── OFF regression: byte-identical to today ─────────────────────────────────────

def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    import auth
    import tenancy
    import finance
    importlib.reload(finance)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(finance, "DB", str(tmp_path / "finance.db"))
    monkeypatch.setattr(finance, "_READY", set())
    assert tenancy.multitenant_enabled() is False

    # Even with a tenant set, OFF keeps scope_clause/write_tenant inert.
    tenancy.set_tenant("A")
    finance.request_advance("CLAIM-A", 1000.0, 20.0, actor="alice")
    tenancy.reset_tenant()

    con = finance.connect()
    try:
        row = con.execute(
            "SELECT tenant_id FROM advances WHERE claim_key='CLAIM-A'").fetchone()
        assert row["tenant_id"] == "default"   # stamped the column DEFAULT
    finally:
        con.close()

    # Reads are unscoped: visible regardless of any thread tenant.
    tenancy.set_tenant("ZZZ")
    try:
        keys = {r["claim_key"] for r in finance.list_advances()}
        assert keys == {"CLAIM-A"}
    finally:
        tenancy.reset_tenant()
