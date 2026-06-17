"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for versioning.py (document version chains).

Behind the `multitenant` switch a tenant's document version chains are isolated:
  * WRITES stamp the bound tenant (tenancy.write_tenant());
  * READS filter by tenancy.scope_clause(): as tenant A versions_for / current / get_version /
    has_chain see ONLY A's chain — B's version of the SAME subject_ref is ABSENT. The platform
    OWNER sees BOTH. add_version / revert_to operate only on A's own chain.

DISTINCT subject_refs per tenant: the ux_doc_versions_sv UNIQUE is on (subject_ref,
version_no), NOT tenant-qualified — so two tenants sharing a subject_ref would collide on the
version_no (a future PK-rekey slice, NOT a read-leak — reads ARE scoped). We use a distinct
ref per tenant exactly as the legacy confidence/suppliers harnesses use distinct keys.

CARDINAL invariant: with the switch OFF (default) writes stamp 'default' and reads are
unscoped — byte-identical to today.
"""
import importlib

import pytest


@pytest.fixture()
def ver(tmp_path, monkeypatch):
    import auth
    import tenancy
    import versioning
    importlib.reload(versioning)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(versioning, "DB", str(tmp_path / "versions.db"))
    monkeypatch.setattr(versioning, "DOCDIR", str(tmp_path / "documents"))
    versioning._SCHEMA_READY.clear()

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    try:
        yield versioning, tenancy
    finally:
        tenancy.reset_tenant()


REF = {"A": "doc:A", "B": "doc:B"}   # distinct logical document ref per tenant


def _seed_two_tenants(versioning, tenancy):
    """Each tenant builds a chain on ITS OWN subject_ref (distinct keys avoid the
    (subject_ref, version_no) UNIQUE clash — a future PK-rekey slice)."""
    for t in ("A", "B"):
        tenancy.set_tenant(t)
        versioning.add_version(REF[t], f"%PDF v1 {t}".encode(), note=f"orig {t}")
        versioning.add_version(REF[t], f"%PDF v2 {t}".encode(), note=f"rev {t}")
    tenancy.reset_tenant()


# ── WRITE stamping ──────────────────────────────────────────────────────────────
def test_writes_stamp_the_bound_tenant(ver):
    versioning, tenancy = ver
    _seed_two_tenants(versioning, tenancy)
    tenancy.set_owner_scope()
    con = versioning.connect()
    try:
        rows = {r["subject_ref"]: r["tenant_id"] for r in con.execute(
            "SELECT subject_ref, tenant_id FROM doc_versions")}
    finally:
        con.close()
    assert rows == {"doc:A": "A", "doc:B": "B"}


# ── READ isolation ──────────────────────────────────────────────────────────────
def test_tenant_a_sees_only_its_chain(ver):
    versioning, tenancy = ver
    _seed_two_tenants(versioning, tenancy)
    tenancy.set_tenant("A")
    chain = versioning.versions_for(REF["A"])
    assert {v["note"] for v in chain} == {"orig A", "rev A"}      # only A's chain
    cur = versioning.current(REF["A"])
    assert cur["note"] == "rev A"
    data, _ = versioning.get_version_bytes(cur["id"])
    assert data == b"%PDF v2 A"
    assert versioning.has_chain(REF["A"]) is True
    # B's chain is INVISIBLE to A (scoped out) even by its own ref
    assert versioning.versions_for(REF["B"]) == []
    assert versioning.current(REF["B"]) is None
    assert versioning.has_chain(REF["B"]) is False


def test_tenant_b_sees_only_its_chain(ver):
    versioning, tenancy = ver
    _seed_two_tenants(versioning, tenancy)
    tenancy.set_tenant("B")
    chain = versioning.versions_for(REF["B"])
    assert {v["note"] for v in chain} == {"orig B", "rev B"}
    assert versioning.current(REF["B"])["note"] == "rev B"
    assert versioning.versions_for(REF["A"]) == []


def test_a_cannot_read_or_revert_bs_version(ver):
    """A get_version on B's version id reads None (scoped); revert_to on it is refused."""
    versioning, tenancy = ver
    _seed_two_tenants(versioning, tenancy)
    tenancy.set_owner_scope()
    con = versioning.connect()
    try:
        b_v1 = con.execute(
            "SELECT id FROM doc_versions WHERE tenant_id='B' AND version_no=1").fetchone()["id"]
    finally:
        con.close()
    tenancy.set_tenant("A")
    assert versioning.get_version(b_v1) is None
    rv, err = versioning.revert_to(b_v1)
    assert rv is None and err == "no such version"
    # A's chain is unchanged (still 2 versions)
    assert len(versioning.versions_for(REF["A"])) == 2


# ── OWNER cross-tenant scope ────────────────────────────────────────────────────
def test_owner_scope_sees_both_chains(ver):
    versioning, tenancy = ver
    _seed_two_tenants(versioning, tenancy)
    tenancy.set_owner_scope()
    assert len(versioning.versions_for(REF["A"])) == 2
    assert len(versioning.versions_for(REF["B"])) == 2


# ── OFF regression ──────────────────────────────────────────────────────────────
def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    import auth
    import tenancy
    import versioning
    importlib.reload(versioning)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(versioning, "DB", str(tmp_path / "versions.db"))
    monkeypatch.setattr(versioning, "DOCDIR", str(tmp_path / "documents"))
    versioning._SCHEMA_READY.clear()
    assert tenancy.multitenant_enabled() is False

    tenancy.set_tenant("A")          # inert while OFF
    versioning.add_version("doc:1", b"%PDF v1", note="orig")
    tenancy.reset_tenant()

    con = versioning.connect()
    try:
        assert con.execute("SELECT tenant_id FROM doc_versions WHERE subject_ref=?",
                           ("doc:1",)).fetchone()["tenant_id"] == "default"
    finally:
        con.close()
    tenancy.set_tenant("ZZZ")
    try:
        assert versioning.current("doc:1")["note"] == "orig"
        assert len(versioning.versions_for("doc:1")) == 1
    finally:
        tenancy.reset_tenant()
