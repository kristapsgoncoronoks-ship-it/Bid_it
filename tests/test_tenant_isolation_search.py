"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for search.py (the FTS5 corpus).

search.py indexes the ENGINE-owned product DBs (invoice_documents in fuel_history.db,
supplier_invoices/suppliers in suppliers.db) READ-ONLY into the app-owned search.db FTS5
index. Those product rows carry the P1 tenant_id column. This slice:
  * STAMPS each FTS row with its SOURCE row's tenant_id during rebuild() (defaulting to
    DEFAULT_TENANT_ID when a product table predates the column — backward compatible);
  * FILTERS search() by tenancy.scope_clause("tenant_id") (tenant_id is an UNINDEXED FTS5
    column) so as tenant A a query returns ONLY A's documents/invoices — B's are ABSENT. The
    platform OWNER (rebuild runs under owner/no scope) and a search under owner scope see BOTH.

The index is rebuilt across ALL tenants (an admin/engine operation); only the per-request
search() is tenant-filtered.

CARDINAL invariant: with the switch OFF (default) the index carries 'default' and search() is
unscoped — byte-identical to today (the existing test_search.py is the standing OFF proof;
this file adds an explicit OFF assertion).
"""
import importlib
import os
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import db  # noqa: E402

pytestmark = pytest.mark.skipif(db.ENGINE != "sqlite",
                                reason="read-only URI handle is a SQLite mechanism")


def _build_products(tmp_path):
    """A tiny tenant-stamped product corpus: tenant A owns the BP document/invoice, tenant B
    owns the SHELL document/invoice (distinct suppliers per tenant)."""
    fh = str(tmp_path / "fuel_history.db")
    su = str(tmp_path / "suppliers.db")

    fc = sqlite3.connect(fh)
    fc.execute("CREATE TABLE invoice_documents (id INTEGER PRIMARY KEY, entity TEXT, "
               "supplier TEXT, invoice_ref TEXT, filename TEXT, kind TEXT, uploaded_at TEXT, "
               "tenant_id TEXT NOT NULL DEFAULT 'default')")
    fc.execute("INSERT INTO invoice_documents VALUES "
               "(1,'Jupiter AS','BP','INV-A','bp.pdf','original_pdf','2026-05-01','A')")
    fc.execute("INSERT INTO invoice_documents VALUES "
               "(2,'Adverza SIA','SHELL','INV-B','shell.pdf','scan','2026-05-02','B')")
    fc.execute("CREATE TABLE transactions (supplier TEXT, product TEXT, product_group TEXT)")
    fc.commit(); fc.close()

    sc = sqlite3.connect(su)
    sc.execute("CREATE TABLE suppliers (code TEXT, legal_name TEXT, "
               "tenant_id TEXT NOT NULL DEFAULT 'default')")
    sc.executemany("INSERT INTO suppliers VALUES (?,?,?)",
                   [("BP", "British Petroleum Baltics", "A"),
                    ("SHELL", "Shell Eesti", "B")])
    sc.execute("CREATE TABLE supplier_vat_registrations (supplier TEXT, country TEXT, "
               "vat_number TEXT, source TEXT)")
    sc.execute("CREATE TABLE supplier_invoices (supplier TEXT, country TEXT, invoice_no TEXT, "
               "invoice_date TEXT, period TEXT, currency TEXT, gross_total REAL, notes TEXT, "
               "tenant_id TEXT NOT NULL DEFAULT 'default')")
    sc.executemany("INSERT INTO supplier_invoices VALUES (?,?,?,?,?,?,?,?,?)",
                   [("BP", "LT", "INV-A", "2026-04-30", "2026-04", "EUR", 100.0,
                     "alpha diesel", "A"),
                    ("SHELL", "EE", "INV-B", "2026-04-29", "2026-04", "EUR", 200.0,
                     "bravo toll", "B")])
    sc.commit(); sc.close()
    return fh, su


@pytest.fixture()
def srch(tmp_path, monkeypatch):
    import auth
    import tenancy
    import dataproduct
    import search
    importlib.reload(search)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(search, "DB", str(tmp_path / "search.db"))

    fh, su = _build_products(tmp_path)
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setitem(dataproduct._PATHS, "suppliers", su)

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    # rebuild runs unscoped (engine/admin) so it indexes BOTH tenants' rows, each stamped.
    tenancy.reset_tenant()
    search.rebuild()
    try:
        yield search, tenancy
    finally:
        tenancy.reset_tenant()


# ── the index row carries the source tenant_id ──────────────────────────────────
def test_index_rows_carry_source_tenant(srch):
    search, tenancy = srch
    con = sqlite3.connect(search.DB)
    try:
        rows = {r[0]: r[1] for r in con.execute("SELECT rowkey, tenant_id FROM corpus")}
    finally:
        con.close()
    assert rows.get("doc:1") == "A"
    assert rows.get("doc:2") == "B"


# ── READ isolation ──────────────────────────────────────────────────────────────
def test_tenant_a_searches_only_its_corpus(srch):
    search, tenancy = srch
    tenancy.set_tenant("A")
    # A's supplier/invoice resolve; B's (SHELL) is filtered out.
    assert {r["rowkey"] for r in search.search("Petroleum")} >= {"doc:1"}
    assert search.search("Shell") == []          # B's supplier invisible to A
    assert search.search("bravo") == []          # B's invoice note invisible to A
    assert {r["rowkey"] for r in search.search("alpha")} == {"inv:BP|LT|INV-A"}


def test_tenant_b_searches_only_its_corpus(srch):
    search, tenancy = srch
    tenancy.set_tenant("B")
    assert search.search("Petroleum") == []      # A's supplier invisible to B
    assert {r["rowkey"] for r in search.search("Shell")} >= {"doc:2"}
    assert search.search("alpha") == []
    assert {r["rowkey"] for r in search.search("bravo")} == {"inv:SHELL|EE|INV-B"}


# ── OWNER cross-tenant scope ────────────────────────────────────────────────────
def test_owner_scope_searches_all(srch):
    search, tenancy = srch
    tenancy.set_owner_scope()
    assert {r["rowkey"] for r in search.search("Petroleum")} >= {"doc:1"}
    assert {r["rowkey"] for r in search.search("Shell")} >= {"doc:2"}


# ── no-principal fails closed ───────────────────────────────────────────────────
def test_no_principal_fails_closed(srch):
    search, tenancy = srch
    tenancy.reset_tenant()   # switch ON but neither owner nor tenant -> AND 1=0
    assert search.search("Petroleum") == []
    assert search.search("Shell") == []


# ── OFF regression: index carries 'default', search unscoped ────────────────────
def test_switch_off_indexes_default_and_searches_unscoped(tmp_path, monkeypatch):
    import auth
    import tenancy
    import dataproduct
    import search
    importlib.reload(search)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(search, "DB", str(tmp_path / "search.db"))
    fh, su = _build_products(tmp_path)
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setitem(dataproduct._PATHS, "suppliers", su)
    assert tenancy.multitenant_enabled() is False

    search.rebuild()
    # OFF: even with a stray thread tenant, search is unscoped — BOTH tenants' rows return.
    tenancy.set_tenant("ZZZ")
    try:
        assert {r["rowkey"] for r in search.search("Petroleum")} >= {"doc:1"}
        assert {r["rowkey"] for r in search.search("Shell")} >= {"doc:2"}
    finally:
        tenancy.reset_tenant()
