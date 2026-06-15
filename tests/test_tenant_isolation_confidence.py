"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for confidence.py (trust + events).

Mirrors tests/test_tenant_isolation_crm.py (the P2 template) for the app-owned
confidence DB. Proves, behind the `multitenant` switch:

  * record_validation() (the validation/worker path) stamps the tenant via
    tenancy.queue_tenant() on BOTH writes (validation_events + supplier_trust UPSERT);
  * trust()/scoreboard()/recent_events() filter by tenancy.scope_clause() — tenant A
    never sees tenant B's trust/events; the platform OWNER sees BOTH;
  * record_validation uses queue_tenant() (NOT write_tenant): a tenant-less call under
    the switch does NOT raise — it stamps DEFAULT_TENANT_ID (queue_tenant's contract);
  * with the switch OFF (default) writes stamp 'default' and reads are unscoped —
    byte-identical to today.

NOTE: supplier_trust's UNIQUE/ON CONFLICT target is (supplier, country), NOT
tenant-qualified — so to keep two tenants' rows distinct here each tenant uses a
DIFFERENT (supplier, country) pair. The collision on a SHARED pair under the switch ON
is the known go-live blocker flagged in confidence.py; it is out of scope for this slice.
"""
import importlib

import pytest


@pytest.fixture()
def conf(tmp_path, monkeypatch):
    """A fresh confidence.db + security.db with the `multitenant` switch ON."""
    import auth
    import tenancy
    import confidence
    importlib.reload(confidence)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(confidence, "DB", str(tmp_path / "confidence.db"))

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True

    try:
        yield confidence, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_two_tenants(confidence, tenancy):
    """As tenant A record a clean validation for (Neste, LV); as tenant B for (Circle, LT)
    — via the REAL record_validation write path, proving both INSERTs stamp the tenant.
    Distinct (supplier, country) pairs avoid the known (supplier, country) UNIQUE clash."""
    tenancy.set_tenant("A")
    confidence.record_validation("Neste", "LV", True, source="test")
    tenancy.set_tenant("B")
    confidence.record_validation("Circle", "LT", True, source="test")
    tenancy.reset_tenant()


# ── WRITE stamping ──────────────────────────────────────────────────────────────

def test_record_validation_stamps_the_bound_tenant(conf):
    confidence, tenancy = conf
    _seed_two_tenants(confidence, tenancy)
    tenancy.set_owner_scope()
    con = confidence.connect()
    try:
        trust_rows = {r["supplier"]: r["tenant_id"]
                      for r in con.execute("SELECT supplier, tenant_id FROM supplier_trust")}
        event_rows = {r["supplier"]: r["tenant_id"]
                      for r in con.execute("SELECT supplier, tenant_id FROM validation_events")}
    finally:
        con.close()
    assert trust_rows == {"Neste": "A", "Circle": "B"}
    assert event_rows == {"Neste": "A", "Circle": "B"}


# ── READ isolation (the core GDPR proof) ────────────────────────────────────────

def test_tenant_a_sees_only_its_trust_and_events(conf):
    confidence, tenancy = conf
    _seed_two_tenants(confidence, tenancy)
    tenancy.set_tenant("A")
    # A's own pair has grown above INIT; B's pair is invisible -> INIT default.
    assert confidence.trust("Neste", "LV") > confidence.INIT
    assert confidence.trust("Circle", "LT") == confidence.INIT
    assert {r["supplier"] for r in confidence.scoreboard()} == {"Neste"}
    assert {r["supplier"] for r in confidence.recent_events()} == {"Neste"}


def test_tenant_b_sees_only_its_trust_and_events(conf):
    confidence, tenancy = conf
    _seed_two_tenants(confidence, tenancy)
    tenancy.set_tenant("B")
    assert confidence.trust("Circle", "LT") > confidence.INIT
    assert confidence.trust("Neste", "LV") == confidence.INIT
    assert {r["supplier"] for r in confidence.scoreboard()} == {"Circle"}
    assert {r["supplier"] for r in confidence.recent_events()} == {"Circle"}


# ── OWNER cross-tenant scope (the audited analytics exception) ───────────────────

def test_owner_scope_sees_both_tenants(conf):
    confidence, tenancy = conf
    _seed_two_tenants(confidence, tenancy)
    tenancy.set_owner_scope()
    assert {r["supplier"] for r in confidence.scoreboard()} == {"Neste", "Circle"}
    assert {r["supplier"] for r in confidence.recent_events()} == {"Neste", "Circle"}


# ── QUEUE-WRITE contract: tenant-less record_validation does NOT raise ───────────

def test_record_validation_without_tenant_stamps_default(conf):
    confidence, tenancy = conf
    # record_validation uses queue_tenant() (system/worker context): a tenant-less call
    # under the switch ON must NOT raise — it stamps DEFAULT_TENANT_ID instead.
    tenancy.reset_tenant()
    t = confidence.record_validation("System", "EE", True, source="worker")
    assert t > confidence.INIT          # the write succeeded (no raise) and trust grew
    tenancy.set_owner_scope()
    con = confidence.connect()
    try:
        row = con.execute(
            "SELECT tenant_id FROM supplier_trust WHERE supplier='System'").fetchone()
        evt = con.execute(
            "SELECT tenant_id FROM validation_events WHERE supplier='System'").fetchone()
    finally:
        con.close()
    assert row["tenant_id"] == tenancy.DEFAULT_TENANT_ID
    assert evt["tenant_id"] == tenancy.DEFAULT_TENANT_ID


# ── OFF regression: byte-identical to today ─────────────────────────────────────

def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    import auth
    import tenancy
    import confidence
    importlib.reload(confidence)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(confidence, "DB", str(tmp_path / "confidence.db"))
    assert tenancy.multitenant_enabled() is False

    # Even with a tenant set, OFF keeps scope_clause/queue_tenant inert.
    tenancy.set_tenant("A")
    confidence.record_validation("Neste", "LV", True, source="test")
    tenancy.reset_tenant()

    con = confidence.connect()
    try:
        row = con.execute(
            "SELECT tenant_id FROM supplier_trust WHERE supplier='Neste'").fetchone()
        assert row["tenant_id"] == "default"   # stamped the column DEFAULT
        evt = con.execute(
            "SELECT tenant_id FROM validation_events WHERE supplier='Neste'").fetchone()
        assert evt["tenant_id"] == "default"
    finally:
        con.close()

    # Reads are unscoped: visible regardless of any thread tenant.
    tenancy.set_tenant("ZZZ")
    try:
        assert confidence.trust("Neste", "LV") > confidence.INIT
        assert {r["supplier"] for r in confidence.scoreboard()} == {"Neste"}
    finally:
        tenancy.reset_tenant()
