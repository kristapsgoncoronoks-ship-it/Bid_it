"""Multi-tenant search × metadata enrichment — search.rebuild() must index EVERY tenant's
documents WITH their A3 metadata, under OWNER scope, and stamp each FTS row with its source
document's tenant_id so search() stays tenant-filtered at query time.

Before this slice the rebuild ran with no tenant bound, so under the `multitenant` switch ON
metadata.tags_for / get_values failed CLOSED (scope_clause -> " AND 1=0") and a document's
tags/custom fields were silently NOT indexed. Now the rebuild scans under set_owner_scope()
so the metadata reads span all tenants, while each row is stamped with ITS document's tenant.

What this file proves (switch ON):
  * tenant A's document is findable BY ITS TAG and BY ITS CUSTOM-FIELD VALUE — by A;
  * the SAME terms are NOT findable by tenant B (the FTS row is stamped 'A', filtered out);
  * the owner sees both.
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
    """tenant A owns doc:1 (BP), tenant B owns doc:2 (SHELL)."""
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
                   [("BP", "British Petroleum Baltics", "A"), ("SHELL", "Shell Eesti", "B")])
    sc.execute("CREATE TABLE supplier_vat_registrations (supplier TEXT, country TEXT, "
               "vat_number TEXT, source TEXT)")
    sc.execute("CREATE TABLE supplier_invoices (supplier TEXT, country TEXT, invoice_no TEXT, "
               "invoice_date TEXT, period TEXT, currency TEXT, gross_total REAL, notes TEXT, "
               "tenant_id TEXT NOT NULL DEFAULT 'default')")
    sc.executemany("INSERT INTO supplier_invoices VALUES (?,?,?,?,?,?,?,?,?)",
                   [("BP", "LT", "INV-A", "2026-04-30", "2026-04", "EUR", 100.0, "n", "A"),
                    ("SHELL", "EE", "INV-B", "2026-04-29", "2026-04", "EUR", 200.0, "n", "B")])
    sc.commit(); sc.close()
    return fh, su


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import auth
    import tenancy
    import dataproduct
    import metadata
    import search
    importlib.reload(metadata)
    importlib.reload(search)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(search, "DB", str(tmp_path / "search.db"))
    monkeypatch.setattr(metadata, "DB", str(tmp_path / "metadata.db"))
    metadata._SCHEMA_READY.clear()

    fh, su = _build_products(tmp_path)
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setitem(dataproduct._PATHS, "suppliers", su)

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True

    # tenant A tags doc:1 and sets a custom field on it.
    tenancy.set_tenant("A")
    tag, _ = metadata.create_tag("Zebrastripe")            # a distinctive tag term
    metadata.assign_tag(tag["id"], "doc:1")
    fld, _ = metadata.define_field("Project", "text")
    metadata.set_value(fld["id"], "doc:1", "Quokkaproject")  # distinctive field value
    tenancy.reset_tenant()

    # rebuild as the engine/owner (the production path): indexes BOTH tenants WITH metadata.
    tenancy.reset_tenant()
    search.rebuild()
    try:
        yield search, tenancy
    finally:
        tenancy.reset_tenant()


def test_owning_tenant_finds_doc_by_tag_and_field(env):
    search, tenancy = env
    tenancy.set_tenant("A")
    assert {r["rowkey"] for r in search.search("Zebrastripe")} == {"doc:1"}
    assert {r["rowkey"] for r in search.search("Quokkaproject")} == {"doc:1"}
    tenancy.reset_tenant()


def test_other_tenant_cannot_find_by_metadata(env):
    search, tenancy = env
    tenancy.set_tenant("B")
    assert search.search("Zebrastripe") == []      # A's tag invisible to B
    assert search.search("Quokkaproject") == []    # A's field value invisible to B
    tenancy.reset_tenant()


def test_owner_sees_the_metadata_terms(env):
    search, tenancy = env
    tenancy.set_owner_scope()
    assert {r["rowkey"] for r in search.search("Zebrastripe")} == {"doc:1"}
    assert {r["rowkey"] for r in search.search("Quokkaproject")} == {"doc:1"}
    tenancy.reset_tenant()


def test_metadata_term_stamped_on_source_tenant_row(env):
    """The FTS row carrying the metadata text is the doc:1 row, stamped tenant 'A'."""
    search, tenancy = env
    con = sqlite3.connect(search.DB)
    try:
        row = con.execute(
            "SELECT tenant_id, body FROM corpus WHERE rowkey='doc:1'").fetchone()
    finally:
        con.close()
    assert row[0] == "A"
    assert "Zebrastripe" in row[1] and "Quokkaproject" in row[1]
