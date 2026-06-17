"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for esign.py (SES signature requests).

Behind the `multitenant` switch, a tenant's signature requests and recorded signatures
are isolated:
  * WRITES stamp the bound tenant (tenancy.write_tenant());
  * READS filter by tenancy.scope_clause(): as tenant A get_request / list_requests /
    signatures_for / get_signature / void_request see ONLY A's rows — B is ABSENT. The
    platform OWNER sees BOTH.

The SES SIGNING happens on a PUBLIC route (/s/<token>/sign) with no session tenant; the app
binds the share link's tenant first (see test_tenant_isolation_sharing) so record_signature
(which resolves the request via the scoped get_request) runs under that tenant. We exercise
record_signature here under an explicit bound tenant, mirroring that.

CARDINAL invariant: with the switch OFF (default) writes stamp 'default' and reads are
unscoped — byte-identical to today.
"""
import importlib
import io

import pytest


def _pdf():
    from pypdf import PdfWriter, PageObject
    w = PdfWriter()
    w.add_page(PageObject.create_blank_page(width=300, height=300))
    b = io.BytesIO(); w.write(b); return b.getvalue()


@pytest.fixture()
def es(tmp_path, monkeypatch):
    import auth
    import tenancy
    import esign
    importlib.reload(esign)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(esign, "DB", str(tmp_path / "esign.db"))
    esign._SCHEMA_READY.clear()
    # keep produced signed PDFs in a throwaway vault dir
    monkeypatch.setattr(esign, "_docdir", lambda: str(tmp_path / "documents"))

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    try:
        yield esign, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_two_tenants(esign, tenancy):
    out = {}
    body = _pdf()
    for t in ("A", "B"):
        tenancy.set_tenant(t)
        req, _ = esign.create_request(f"doc:{t}", f"Contract {t}", f"user{t}")
        sig, err = esign.record_signature(req["id"], f"Signer {t}", body,
                                          signer_email=f"s{t}@x.com", ip="1.1.1.1",
                                          user_agent="agent")
        assert sig, err
        out[t] = {"req": req, "sig": sig}
    tenancy.reset_tenant()
    return out


# ── WRITE stamping ──────────────────────────────────────────────────────────────
def test_writes_stamp_the_bound_tenant(es):
    esign, tenancy = es
    _seed_two_tenants(esign, tenancy)
    tenancy.set_owner_scope()
    con = esign.connect()
    try:
        reqs = {r["title"]: r["tenant_id"]
                for r in con.execute("SELECT title, tenant_id FROM signature_requests")}
        sigs = {r["signer_name"]: r["tenant_id"]
                for r in con.execute("SELECT signer_name, tenant_id FROM signatures")}
    finally:
        con.close()
    assert reqs == {"Contract A": "A", "Contract B": "B"}
    assert sigs == {"Signer A": "A", "Signer B": "B"}


# ── READ isolation ──────────────────────────────────────────────────────────────
def test_tenant_a_sees_only_its_requests(es):
    esign, tenancy = es
    seeded = _seed_two_tenants(esign, tenancy)
    tenancy.set_tenant("A")
    assert {r["title"] for r in esign.list_requests()} == {"Contract A"}
    assert esign.get_request(seeded["A"]["req"]["id"]) is not None
    assert esign.get_request(seeded["B"]["req"]["id"]) is None
    assert {s["signer_name"] for s in esign.signatures_for(seeded["A"]["req"]["id"])} \
        == {"Signer A"}
    assert esign.signatures_for(seeded["B"]["req"]["id"]) == []
    assert esign.get_signature(seeded["A"]["sig"]["id"]) is not None
    assert esign.get_signature(seeded["B"]["sig"]["id"]) is None
    # void scoped: A cannot void B's request
    ok, _ = esign.void_request(seeded["B"]["req"]["id"], "userA")
    assert ok is False


def test_tenant_b_sees_only_its_requests(es):
    esign, tenancy = es
    seeded = _seed_two_tenants(esign, tenancy)
    tenancy.set_tenant("B")
    assert {r["title"] for r in esign.list_requests()} == {"Contract B"}
    assert esign.get_request(seeded["A"]["req"]["id"]) is None
    assert esign.signatures_for(seeded["A"]["req"]["id"]) == []


# ── OWNER cross-tenant scope ────────────────────────────────────────────────────
def test_owner_scope_sees_both_tenants(es):
    esign, tenancy = es
    _seed_two_tenants(esign, tenancy)
    tenancy.set_owner_scope()
    assert {r["title"] for r in esign.list_requests()} == {"Contract A", "Contract B"}


# ── OFF regression ──────────────────────────────────────────────────────────────
def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    import auth
    import tenancy
    import esign
    importlib.reload(esign)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(esign, "DB", str(tmp_path / "esign.db"))
    esign._SCHEMA_READY.clear()
    monkeypatch.setattr(esign, "_docdir", lambda: str(tmp_path / "documents"))
    assert tenancy.multitenant_enabled() is False

    tenancy.set_tenant("A")          # inert while OFF
    req, _ = esign.create_request("doc:x", "Contract", "user")
    sig, err = esign.record_signature(req["id"], "Signer", _pdf(), ip="1.1.1.1")
    assert sig, err
    tenancy.reset_tenant()

    con = esign.connect()
    try:
        assert con.execute("SELECT tenant_id FROM signature_requests WHERE id=?",
                           (req["id"],)).fetchone()["tenant_id"] == "default"
    finally:
        con.close()
    tenancy.set_tenant("ZZZ")
    try:
        assert esign.get_request(req["id"]) is not None
        assert {r["title"] for r in esign.list_requests()} == {"Contract"}
    finally:
        tenancy.reset_tenant()
