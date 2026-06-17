"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for retention.py (policies + legal holds).

Behind the `multitenant` switch a tenant's retention policies and legal holds are isolated:
  * WRITES stamp the bound tenant (tenancy.write_tenant());
  * READS filter by tenancy.scope_clause(): as tenant A list_policies / holds_for / is_on_hold
    and the due_for_review worklist (which resolves policies via list_policies + tag scope) see
    ONLY A's rows — B is ABSENT. The platform OWNER sees BOTH.

A's legal hold on a document does NOT suppress B's retention review of the SAME document ref
(holds are tenant-scoped), and vice versa — a tenant's hold protects only its own records.

CARDINAL invariant: with the switch OFF (default) writes stamp 'default' and reads are
unscoped — byte-identical to today.
"""
import importlib

import pytest


@pytest.fixture()
def ret(tmp_path, monkeypatch):
    import auth
    import tenancy
    import retention
    importlib.reload(retention)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(retention, "DB", str(tmp_path / "retention.db"))
    retention._SCHEMA_READY.clear()
    # inject the document corpus directly (no product DB), shared across tenants.
    monkeypatch.setitem(
        retention.__dict__, "_DOC_SOURCE",
        lambda: [{"subject_ref": "doc:1", "doc_id": 1, "entity": "E", "supplier": "S",
                  "invoice_ref": "INV", "filename": "f.pdf",
                  "doc_date": "2000-01-01", "registered_date": "2000-01-01"}])

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    try:
        yield retention, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_two_tenants(retention, tenancy):
    out = {}
    for t in ("A", "B"):
        tenancy.set_tenant(t)
        pol, _ = retention.define_policy(f"Policy {t}", "all", retain_years=10)
        hold, _ = retention.place_hold("doc:1", f"hold {t}", f"user{t}")
        out[t] = {"policy": pol, "hold": hold}
    tenancy.reset_tenant()
    return out


# ── WRITE stamping ──────────────────────────────────────────────────────────────
def test_writes_stamp_the_bound_tenant(ret):
    retention, tenancy = ret
    _seed_two_tenants(retention, tenancy)
    tenancy.set_owner_scope()
    con = retention.connect()
    try:
        pols = {r["name"]: r["tenant_id"]
                for r in con.execute("SELECT name, tenant_id FROM retention_policies")}
        holds = {r["reason"]: r["tenant_id"]
                 for r in con.execute("SELECT reason, tenant_id FROM legal_holds")}
    finally:
        con.close()
    assert pols == {"Policy A": "A", "Policy B": "B"}
    assert holds == {"hold A": "A", "hold B": "B"}


# ── READ isolation ──────────────────────────────────────────────────────────────
def test_tenant_a_sees_only_its_policies_and_holds(ret):
    retention, tenancy = ret
    seeded = _seed_two_tenants(retention, tenancy)
    tenancy.set_tenant("A")
    assert {p["name"] for p in retention.list_policies()} == {"Policy A"}
    assert {h["reason"] for h in retention.holds_for("doc:1")} == {"hold A"}
    assert retention.is_on_hold("doc:1") is True
    # A cannot release B's hold (scoped UPDATE -> no such hold)
    ok, _ = retention.release_hold(seeded["B"]["hold"]["id"], "userA")
    assert ok is False
    # A's hold still stands
    assert retention.is_on_hold("doc:1") is True


def test_tenant_b_sees_only_its_policies(ret):
    retention, tenancy = ret
    _seed_two_tenants(retention, tenancy)
    tenancy.set_tenant("B")
    assert {p["name"] for p in retention.list_policies()} == {"Policy B"}
    assert {h["reason"] for h in retention.holds_for("doc:1")} == {"hold B"}


def test_hold_scoping_does_not_cross_tenants(ret):
    """B releases ITS hold; A's hold still suppresses A's review — independent per tenant."""
    retention, tenancy = ret
    seeded = _seed_two_tenants(retention, tenancy)
    # B releases its own hold -> B's doc is now due for review (10y elapsed from 2000)
    tenancy.set_tenant("B")
    ok, _ = retention.release_hold(seeded["B"]["hold"]["id"], "userB")
    assert ok
    assert retention.is_on_hold("doc:1") is False
    assert {d["subject_ref"] for d in retention.due_for_review()} == {"doc:1"}
    # A's hold is untouched -> A's doc is NOT due (held), and B's release didn't leak
    tenancy.set_tenant("A")
    assert retention.is_on_hold("doc:1") is True
    assert retention.due_for_review() == []


# ── OWNER cross-tenant scope ────────────────────────────────────────────────────
def test_owner_scope_sees_both_tenants(ret):
    retention, tenancy = ret
    _seed_two_tenants(retention, tenancy)
    tenancy.set_owner_scope()
    assert {p["name"] for p in retention.list_policies()} == {"Policy A", "Policy B"}
    assert {h["reason"] for h in retention.holds_for("doc:1")} == {"hold A", "hold B"}


# ── OFF regression ──────────────────────────────────────────────────────────────
def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    import auth
    import tenancy
    import retention
    importlib.reload(retention)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(retention, "DB", str(tmp_path / "retention.db"))
    retention._SCHEMA_READY.clear()
    assert tenancy.multitenant_enabled() is False

    tenancy.set_tenant("A")          # inert while OFF
    pol, _ = retention.define_policy("Policy", "all", retain_years=10)
    hold, _ = retention.place_hold("doc:1", "hold", "user")
    tenancy.reset_tenant()

    con = retention.connect()
    try:
        assert con.execute("SELECT tenant_id FROM retention_policies WHERE id=?",
                           (pol["id"],)).fetchone()["tenant_id"] == "default"
        assert con.execute("SELECT tenant_id FROM legal_holds WHERE id=?",
                           (hold["id"],)).fetchone()["tenant_id"] == "default"
    finally:
        con.close()
    tenancy.set_tenant("ZZZ")
    try:
        assert {p["name"] for p in retention.list_policies()} == {"Policy"}
        assert retention.is_on_hold("doc:1") is True
    finally:
        tenancy.reset_tenant()
