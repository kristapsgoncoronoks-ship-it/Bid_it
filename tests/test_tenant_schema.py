"""Multi-tenancy P1 — schema plumbing (customers.db CRM slice).

Proves the REUSABLE tenant-column mechanism (`tenancy.tenant_column_ddls`) and its
first wiring into customer_master, with the CARDINAL invariant: adding a
`tenant_id TEXT NOT NULL DEFAULT 'default'` column that NO query reads/filters
changes no behavior. The `multitenant` switch stays OFF and scope_clause is NOT
wired into any query yet (that's P2). Existing + new rows backfill to the default
tenant via the column DEFAULT.
"""
import importlib

import pytest


def test_tenant_column_ddls_shape():
    """The reusable helper emits the IDENTICAL column definition per table."""
    import tenancy
    assert tenancy.DEFAULT_TENANT_ID == "default"
    assert tenancy.TENANT_COLUMN == "tenant_id"
    assert tenancy.tenant_column_ddls(["t"]) == [
        "ALTER TABLE t ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
    ]
    # one statement per table, in order
    ddls = tenancy.tenant_column_ddls(["a", "b", "c"])
    assert len(ddls) == 3
    assert all(d.startswith("ALTER TABLE ") and
               "ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'" in d
               for d in ddls)
    assert ddls[1] == "ALTER TABLE b ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"


def _fresh_cm(tmp_path, monkeypatch):
    import customer_master
    importlib.reload(customer_master)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "cdocs"))
    return customer_master


# Every CRM table that P1 stamps (must mirror the list wired in customer_master.connect).
CRM_TABLES = [
    "customers", "customer_bank_accounts", "customer_supplier_accounts",
    "customer_documents", "customer_fees", "customer_countries",
    "country_requirements", "checklist_rules", "doc_templates", "document_requests",
]


def test_every_crm_table_has_tenant_id_default(tmp_path, monkeypatch):
    """After connect() on a fresh DB, every CRM table carries a tenant_id column
    whose declared DEFAULT is the default tenant."""
    cm = _fresh_cm(tmp_path, monkeypatch)
    con = cm.connect()
    try:
        for table in CRM_TABLES:
            cols = {r["name"]: r for r in
                    con.execute(f"PRAGMA table_info({table})").fetchall()}
            assert "tenant_id" in cols, f"{table} missing tenant_id"
            col = cols["tenant_id"]
            assert col["type"] == "TEXT", f"{table}.tenant_id type {col['type']!r}"
            assert col["notnull"] == 1, f"{table}.tenant_id should be NOT NULL"
            # PRAGMA reports the DEFAULT literal including the quotes.
            assert col["dflt_value"] == "'default'", \
                f"{table}.tenant_id default {col['dflt_value']!r}"
    finally:
        con.close()


def test_default_backfills_unspecified_insert(tmp_path, monkeypatch):
    """An INSERT that does NOT name tenant_id yields tenant_id == 'default' — the
    same DEFAULT that backfills existing rows when the ALTER first runs."""
    cm = _fresh_cm(tmp_path, monkeypatch)
    con = cm.connect()
    try:
        con.execute(
            "INSERT INTO customers (code, company_name) VALUES ('RAW', 'Raw Co')")
        con.commit()
        assert con.execute(
            "SELECT tenant_id FROM customers WHERE code='RAW'").fetchone()[0] == "default"
    finally:
        con.close()


def test_add_customer_api_stamps_default_tenant(tmp_path, monkeypatch):
    """The real insert path (add_customer) — which lists columns explicitly and
    does NOT mention tenant_id — produces a row stamped with the default tenant."""
    cm = _fresh_cm(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", country="LV")
    con = cm.connect()
    try:
        row = con.execute(
            "SELECT tenant_id, status FROM customers WHERE code='ACME'").fetchone()
        assert row["tenant_id"] == "default"
        assert row["status"] == "pending"   # behavior unchanged
    finally:
        con.close()


def test_idempotent_second_connect(tmp_path, monkeypatch):
    """db_migrate runs the ALTER once: a second connect on the SAME DB file (fresh
    process-cache) does not error or duplicate the column."""
    cm = _fresh_cm(tmp_path, monkeypatch)
    cm.connect().close()
    # Clear the in-process schema-ready cache so connect() re-enters the migration
    # path against the EXISTING file; db_migrate's versioned table must skip the
    # already-applied ALTERs rather than re-running them (which would raise
    # "duplicate column name").
    monkeypatch.setattr(cm, "_SCHEMA_READY", set())
    con = cm.connect()   # must NOT raise
    try:
        cols = [r["name"] for r in
                con.execute("PRAGMA table_info(customers)").fetchall()]
        assert cols.count("tenant_id") == 1
    finally:
        con.close()
