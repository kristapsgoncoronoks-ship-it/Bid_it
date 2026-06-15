"""Multi-tenancy P1 — schema plumbing (slice B1: suppliers.db / supplier_master.py).

Extends the proven CRM + A1 + A2 slices (test_tenant_schema.py /
test_tenant_schema_a1.py / test_tenant_schema_a2.py) to the ENGINE-owned supplier
master DB. suppliers.db is engine-owned: the app reads it READ-ONLY via dataproduct,
the ENGINE/worker writes it via supplier_master.connect() — so appending the tenant_id
ALTER to supplier_master's db_migrate list runs the column on the WRITABLE engine path
(correct).

Same cardinal invariant as every P1 slice: adding a
`tenant_id TEXT NOT NULL DEFAULT 'default'` column that NO query reads/filters changes
no behavior. The `multitenant` switch stays OFF and scope_clause is NOT wired into any
query (that's P2). Existing + new rows backfill to the default tenant via the column
DEFAULT.

Each section proves: (a) every named tenant table carries the tenant_id column with the
default; (b) an INSERT that doesn't name tenant_id backfills 'default'; (c) the explicit-
column seed() path runs and stamps 'default' (the positional-INSERT fix); (d) a second
connect on the SAME file is idempotent (the ALTER runs once); (e) the engine worker's
register_statement() write path still works and stamps the new column on
supplier_statements / statement_invoices / supplier_invoices.

discount_rules() is a SELECT* read but is consumed only by NAMED keys (contract_audit /
app.py recovery + contract-price-terms) — NOT a column-set contract — so its dict
deliberately keeps the inert tenant_id key (a judgment recorded by
test_discount_rules_dict_is_by_key_not_a_contract below).
"""
import importlib

import pytest


# The eight tenant-owned tables stamped in suppliers.db (supplier_master DDL list).
SUPPLIER_TENANT_TABLES = [
    "suppliers", "supplier_bank_accounts", "supplier_vat_registrations",
    "supplier_discounts", "supplier_invoices", "supplier_products",
    "supplier_statements", "statement_invoices",
]


def _assert_tenant_col(con, table):
    cols = {r["name"]: r for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    assert "tenant_id" in cols, f"{table} missing tenant_id"
    col = cols["tenant_id"]
    assert col["type"] == "TEXT", f"{table}.tenant_id type {col['type']!r}"
    assert col["notnull"] == 1, f"{table}.tenant_id should be NOT NULL"
    assert col["dflt_value"] == "'default'", \
        f"{table}.tenant_id default {col['dflt_value']!r}"


def _fresh_supplier_master(tmp_path, monkeypatch):
    import supplier_master
    importlib.reload(supplier_master)
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    return supplier_master


def test_supplier_tables_have_tenant_id_default(tmp_path, monkeypatch):
    sm = _fresh_supplier_master(tmp_path, monkeypatch)
    con = sm.connect()
    try:
        for table in SUPPLIER_TENANT_TABLES:
            _assert_tenant_col(con, table)
    finally:
        con.close()


def test_supplier_default_backfills_unspecified_insert(tmp_path, monkeypatch):
    """A suppliers row inserted without naming tenant_id backfills 'default'."""
    sm = _fresh_supplier_master(tmp_path, monkeypatch)
    con = sm.connect()
    try:
        con.execute("INSERT INTO suppliers (code, legal_name) VALUES ('ZZ', 'Ztest')")
        con.commit()
        assert con.execute(
            "SELECT tenant_id FROM suppliers WHERE code='ZZ'").fetchone()[0] == "default"
    finally:
        con.close()


def test_seed_explicit_columns_runs_and_stamps_default(tmp_path, monkeypatch):
    """The seed() path (now EXPLICIT-column, not positional VALUES) runs without a
    column-count mismatch and stamps 'default' across every seeded table — this is
    the positional-INSERT fix the trailing tenant_id column required."""
    sm = _fresh_supplier_master(tmp_path, monkeypatch)
    con = sm.connect()
    try:
        sm.seed(con)   # must NOT raise a "table has N columns but M values" error
        for table in ("suppliers", "supplier_vat_registrations",
                      "supplier_bank_accounts", "supplier_products",
                      "supplier_invoices"):
            stamps = {r[0] for r in
                      con.execute(f"SELECT DISTINCT tenant_id FROM {table}")}
            assert stamps == {"default"}, f"{table} stamps {stamps!r}"
        # Sanity: the seed actually populated rows (e.g. the well-known Q8 supplier).
        assert con.execute(
            "SELECT COUNT(*) FROM suppliers WHERE code='Q8'").fetchone()[0] == 1
    finally:
        con.close()


def test_set_discount_rule_stamps_default(tmp_path, monkeypatch):
    """set_discount_rule() (explicit-column INSERT) stamps the new row 'default'."""
    sm = _fresh_supplier_master(tmp_path, monkeypatch)
    rid = sm.set_discount_rule("DKV", expected_discount_eur_l=0.13, note="t")
    con = sm.connect()
    try:
        assert con.execute(
            "SELECT tenant_id FROM supplier_discounts WHERE id=?", (rid,)
        ).fetchone()[0] == "default"
    finally:
        con.close()


def test_discount_rules_dict_is_by_key_not_a_contract(tmp_path, monkeypatch):
    """discount_rules() is a SELECT* read, so its dict carries the inert tenant_id
    key. That is DELIBERATELY left (not stripped): every consumer reads it by NAMED
    keys (contract_audit, app.py recovery / contract-price-terms), never as an exact
    column-set contract, so the extra key is inert. This test pins that judgment."""
    sm = _fresh_supplier_master(tmp_path, monkeypatch)
    sm.set_discount_rule("DKV", expected_discount_eur_l=0.13, note="t")
    rules = sm.discount_rules()
    assert rules and rules[0]["supplier"] == "DKV"
    # by-key consumption is unaffected; the inert column is present but read by no query
    assert rules[0]["expected_discount_eur_l"] == 0.13
    assert rules[0]["tenant_id"] == "default"


def test_register_statement_stamps_default(tmp_path, monkeypatch):
    """The engine worker's register_statement() write path still works and stamps the
    new tenant_id column on supplier_statements / statement_invoices / supplier_invoices
    (its INSERTs are explicit-column, so the trailing tenant_id takes its DEFAULT)."""
    sm = _fresh_supplier_master(tmp_path, monkeypatch)
    sm.connect().close()   # materialize the schema (incl. tenant_id) on the tmp DB
    import invoice_control
    importlib.reload(invoice_control)
    invoice_control.register_statement(
        "DKV", "STMT-1", "2026-05", "2026-05-31",
        lines=[("INV-1", "2026-05-15", "Sweden", "SEK", 100.0, 25.0)],
        notes="t", customer="DEMO")
    con = sm.connect()
    try:
        assert con.execute(
            "SELECT tenant_id FROM supplier_statements WHERE statement_ref='STMT-1'"
        ).fetchone()[0] == "default"
        assert con.execute(
            "SELECT tenant_id FROM statement_invoices WHERE statement_ref='STMT-1'"
        ).fetchone()[0] == "default"
        # vat>0 auto-synced a supplier_invoices row, which must also stamp 'default'
        assert con.execute(
            "SELECT tenant_id FROM supplier_invoices WHERE invoice_no='INV-1'"
        ).fetchone()[0] == "default"
    finally:
        con.close()


def test_supplier_idempotent_second_connect(tmp_path, monkeypatch):
    sm = _fresh_supplier_master(tmp_path, monkeypatch)
    sm.connect().close()
    sm._SCHEMA_READY.clear()
    con = sm.connect()   # must NOT raise (db_migrate skips the applied ALTERs)
    try:
        for table in SUPPLIER_TENANT_TABLES:
            cols = [r["name"] for r in
                    con.execute(f"PRAGMA table_info({table})").fetchall()]
            assert cols.count("tenant_id") == 1, f"{table} duplicated tenant_id"
    finally:
        con.close()
