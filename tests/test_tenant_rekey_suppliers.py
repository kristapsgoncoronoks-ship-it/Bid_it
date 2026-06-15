"""Multi-tenant PK-REKEY — tenant-qualified PRIMARY KEYs for the engine-owned
suppliers.db (7 natural-key tables: 5 AUDITED + 2 unaudited statement tables).

Follows the already-approved templates: benchmark.db (un-audited) and portal.db / CRM
customers.db (AUDITED — tests/test_tenant_rekey_customers.py). The suppliers.db
natural-key tables already carry a tenant_id column (P1) and tenant-scoped reads /
stamped writes (P2), but their PRIMARY KEYs did NOT include tenant_id, so the seeders'
INSERT OR REPLACE, set_vat_registration's ON CONFLICT(supplier, country) and
register_statement's INSERT OR REPLACE resolved on the NATURAL key only. Under the
`multitenant` switch ON, two tenants writing the same code / iban / (supplier, country) /
(supplier, invoice_no) / (supplier, statement_ref) would COLLIDE and overwrite each
other's supplier-master rows — cross-tenant data loss. This slice rebuilds each of the 7
tables with tenant_id FIRST in the PK (LAST in the column list).

The 7 rebuilt tables and their old->new PK (and, for the 5 audited ones, the preserved
natural audit rowkey == audit._cols_pk pks[0]):
    suppliers                  PK (code)                          -> (tenant_id, code)                          rowkey code   [AUDITED]
    supplier_vat_registrations PK (supplier, country)             -> (tenant_id, supplier, country)             rowkey supplier [AUDITED]
    supplier_bank_accounts     PK (iban)                          -> (tenant_id, iban)                          rowkey iban   [AUDITED]
    supplier_products          PK (supplier, product_code, name)  -> (tenant_id, supplier, product_code, name)  rowkey supplier [AUDITED]
    supplier_invoices          PK (supplier, invoice_no)          -> (tenant_id, supplier, invoice_no)          rowkey supplier [AUDITED]
    supplier_statements        PK (supplier, statement_ref)       -> (tenant_id, supplier, statement_ref)       [UNAUDITED]
    statement_invoices         PK (supplier, statement_ref, inv)  -> (tenant_id, supplier, statement_ref, inv)  [UNAUDITED]

What this file proves:
  * NO CROSS-TENANT CLOBBER driving the REAL writers (seed / set_vat_registration /
    register_statement): A and B can hold the SAME code / (supplier,country) /
    (supplier,invoice_no) / (supplier,statement_ref) and each reads its OWN.
  * AUDIT SURVIVES THE REBUILD on the 5 audited tables: aud_<t>_i/u/d exist and a write
    logs an audit_log row keyed by the PRESERVED natural rowkey (NOT tenant_id/'default').
    The 2 statement tables have NO aud_ triggers (unchanged).
  * EXISTING-DB PRESERVATION: an OLD-schema (natural PKs + P1 tenant_id) populated
    suppliers.db rebuilt via connect() keeps per-table row counts and tenant-qualifies
    every PK.
  * OFF byte-identical: a same-key write corrects in place under the single 'default'
    tenant.
"""
import importlib
import sqlite3

import pytest


def _fresh_env(tmp_path, monkeypatch, switch):
    """Wire a fresh suppliers.db / security.db and set the multitenant switch.

    CRITICALLY clears BOTH supplier_master._SCHEMA_READY AND audit._AUDIT_INSTALLED so
    connect() runs schema + migrations + install_audit exactly like a fresh process —
    the audit triggers are then (re)created against the rekeyed tables. Returns
    (supplier_master, invoice_control, tenancy, audit)."""
    import auth
    import tenancy
    import audit
    import supplier_master
    import invoice_control as IC
    importlib.reload(supplier_master)
    importlib.reload(IC)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    monkeypatch.setattr(supplier_master, "_SCHEMA_READY", set())
    audit._AUDIT_INSTALLED.clear()   # mirror a fresh process: install_audit must RUN

    auth.set_setting("multitenant", "1" if switch else "0")
    assert tenancy.multitenant_enabled() is switch
    return supplier_master, IC, tenancy, audit


@pytest.fixture()
def on(tmp_path, monkeypatch):
    """multitenant switch ON."""
    sm, IC, tn, ad = _fresh_env(tmp_path, monkeypatch, switch=True)
    try:
        yield sm, IC, tn, ad
    finally:
        tn.reset_tenant()


# ── The core fix: NO cross-tenant clobber (driving the REAL writers) ─────────────

def test_suppliers_no_cross_tenant_clobber(on):
    """seed() with the SAME code 'SHARED' as tenant A and tenant B — both rows persist
    (the tenant-qualified PK), each tenant reads its OWN legal_name."""
    sm, IC, tn, _ = on
    tn.set_tenant("A")
    con = sm.connect()
    try:
        con.execute("INSERT OR REPLACE INTO suppliers (code, legal_name, tenant_id) "
                    "VALUES ('SHARED','Acme A',?)", (tn.queue_tenant(),))
        con.commit()
    finally:
        con.close()
    tn.set_tenant("B")
    con = sm.connect()
    try:
        con.execute("INSERT OR REPLACE INTO suppliers (code, legal_name, tenant_id) "
                    "VALUES ('SHARED','Beta B',?)", (tn.queue_tenant(),))
        con.commit()
    finally:
        con.close()
    tn.reset_tenant()

    tn.set_owner_scope()
    con = sm.connect()
    try:
        rows = sorted((r["tenant_id"], r["legal_name"]) for r in con.execute(
            "SELECT tenant_id, legal_name FROM suppliers WHERE code='SHARED'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "Acme A"), ("B", "Beta B")]

    # each tenant reads its OWN via the scoped public read (get_issuer)
    tn.set_tenant("A")
    assert sm.get_issuer("SHARED")[0] == "Acme A"
    tn.set_tenant("B")
    assert sm.get_issuer("SHARED")[0] == "Beta B"
    tn.reset_tenant()


def test_supplier_vat_registrations_no_cross_tenant_clobber(on):
    """set_vat_registration (INSERT … ON CONFLICT(tenant_id, supplier, country)) on the
    SAME (supplier, country) as A and B — both persist with own vat_number."""
    sm, IC, tn, _ = on
    tn.set_tenant("A")
    sm.set_vat_registration("SUP", "France", "FR-A", source="A")
    tn.set_tenant("B")
    sm.set_vat_registration("SUP", "France", "FR-B", source="B")
    tn.reset_tenant()

    tn.set_owner_scope()
    con = sm.connect()
    try:
        rows = sorted((r["tenant_id"], r["vat_number"]) for r in con.execute(
            "SELECT tenant_id, vat_number FROM supplier_vat_registrations "
            "WHERE supplier='SUP' AND country='France'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "FR-A"), ("B", "FR-B")]

    # the ON CONFLICT upsert still corrects in place WITHIN a tenant (not a new row)
    tn.set_tenant("A")
    sm.set_vat_registration("SUP", "France", "FR-A2", source="A2")
    assert sm.get_issuer("SUP", "France")[1] == "FR-A2"
    tn.reset_tenant()


def test_supplier_invoices_no_cross_tenant_clobber(on):
    """register_statement (the worker write path) auto-syncs a VAT-bearing line into
    supplier_invoices. The SAME (supplier, invoice_no) as A and B — both persist."""
    sm, IC, tn, _ = on
    tn.set_tenant("A")
    IC.register_statement("SUP", "ST", "2099-09", "2099-09-30",
                          lines=[("INV", "2099-09-16", "France", "EUR", 500.0, 100.0)],
                          customer="Nonexistent Co")
    tn.set_tenant("B")
    IC.register_statement("SUP", "ST", "2099-09", "2099-09-30",
                          lines=[("INV", "2099-09-16", "France", "EUR", 900.0, 200.0)],
                          customer="Nonexistent Co")
    tn.reset_tenant()

    tn.set_owner_scope()
    con = sm.connect()
    try:
        rows = sorted((r["tenant_id"], r["gross_total"]) for r in con.execute(
            "SELECT tenant_id, gross_total FROM supplier_invoices "
            "WHERE supplier='SUP' AND invoice_no='INV'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", 600.0), ("B", 1100.0)]


def test_supplier_statements_no_cross_tenant_clobber(on):
    """register_statement writes supplier_statements (UNAUDITED). The SAME
    (supplier, statement_ref) as A and B — both persist with own period."""
    sm, IC, tn, _ = on
    tn.set_tenant("A")
    IC.register_statement("SUP", "ST", "2099-09", "2099-09-30",
                          lines=[("INVA", "2099-09-16", "France", "EUR", 100.0, 20.0)],
                          customer="Nonexistent Co")
    tn.set_tenant("B")
    IC.register_statement("SUP", "ST", "2099-10", "2099-10-31",
                          lines=[("INVB", "2099-10-16", "France", "EUR", 100.0, 20.0)],
                          customer="Nonexistent Co")
    tn.reset_tenant()

    tn.set_owner_scope()
    con = sm.connect()
    try:
        stmts = sorted((r["tenant_id"], r["period"]) for r in con.execute(
            "SELECT tenant_id, period FROM supplier_statements WHERE statement_ref='ST'"))
        # statement_invoices keep their own (supplier, statement_ref, invoice_no) rows
        lines = sorted((r["tenant_id"], r["invoice_no"]) for r in con.execute(
            "SELECT tenant_id, invoice_no FROM statement_invoices WHERE statement_ref='ST'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert stmts == [("A", "2099-09"), ("B", "2099-10")]
    assert lines == [("A", "INVA"), ("B", "INVB")]


# ── AUDIT survives the rebuild on the 5 audited tables ───────────────────────────

AUDITED = [
    "suppliers", "supplier_vat_registrations", "supplier_bank_accounts",
    "supplier_products", "supplier_invoices",
]
UNAUDITED = ["supplier_statements", "statement_invoices"]
# natural audit rowkey (pks[0]) preserved per audited table
NATURAL_PK = {
    "suppliers": "code", "supplier_vat_registrations": "supplier",
    "supplier_bank_accounts": "iban", "supplier_products": "supplier",
    "supplier_invoices": "supplier",
}


def test_audit_triggers_exist_on_audited_tables_only(on):
    """After a fresh connect (caches cleared in the fixture), aud_<t>_i/u/d must exist
    on every RENAMED AUDITED table — DROP TABLE dropped them, install_audit recreated.
    The 2 unaudited statement tables must have NO aud_ triggers (unchanged)."""
    sm, IC, tn, _ = on
    tn.set_tenant("A")
    con = sm.connect()
    try:
        trigs = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
    finally:
        con.close()
    tn.reset_tenant()
    for t in AUDITED:
        for sfx in ("i", "u", "d"):
            assert f"aud_{t}_{sfx}" in trigs, (t, sfx)
    for t in UNAUDITED:
        for sfx in ("i", "u", "d"):
            assert f"aud_{t}_{sfx}" not in trigs, (t, sfx)


def test_audit_rowkey_is_preserved_natural_key_not_tenant(on):
    """A write to each audited rekeyed table logs an audit_log row keyed by the
    PRESERVED natural rowkey (suppliers->code, supplier_bank_accounts->iban, the rest
    ->supplier) — NOT tenant_id/'default'. This is the column-order vs PK-clause-order
    guarantee (tenant_id FIRST in the PK clause but LAST in the column list, so
    audit._cols_pk pks[0] == the natural key)."""
    sm, IC, tn, ad = on
    tn.set_tenant("A")
    con = sm.connect()
    try:
        con.execute("INSERT INTO suppliers (code, legal_name, tenant_id) "
                    "VALUES ('AUDCO','Audit Co',?)", (tn.queue_tenant(),))
        con.execute("INSERT INTO supplier_bank_accounts (supplier, iban, tenant_id) "
                    "VALUES ('AUDCO','EE-AUD',?)", (tn.queue_tenant(),))
        con.execute("INSERT INTO supplier_products (supplier, product_code, product_name, tenant_id) "
                    "VALUES ('AUDCO','27','Diesel',?)", (tn.queue_tenant(),))
        con.commit()
    finally:
        con.close()
    sm.set_vat_registration("AUDCO", "France", "FR-AUD")     # supplier_vat_registrations
    IC.register_statement("AUDCO", "ST-AUD", "2099-09", "2099-09-30",
                          lines=[("INV-AUD", "2099-09-16", "France", "EUR", 100.0, 20.0)],
                          customer="Nonexistent Co")          # supplier_invoices (auto-sync)
    tn.reset_tenant()

    con = sm.connect()
    try:
        for t in AUDITED:
            natural = NATURAL_PK[t]
            keys = {r["rowkey"] for r in con.execute(
                "SELECT rowkey FROM audit_log WHERE tbl=? AND action='INSERT'", (t,))}
            assert keys, t
            assert "A" not in keys and "default" not in keys, (t, keys)
            _cols, pk0 = ad._cols_pk(con, t)
            assert pk0 == natural, (t, pk0)
        # spot-check exact preserved key values
        sup_keys = {r["rowkey"] for r in con.execute(
            "SELECT rowkey FROM audit_log WHERE tbl='suppliers' AND action='INSERT'")}
        iban_keys = {r["rowkey"] for r in con.execute(
            "SELECT rowkey FROM audit_log WHERE tbl='supplier_bank_accounts' AND action='INSERT'")}
    finally:
        con.close()
    assert "AUDCO" in sup_keys      # suppliers rowkey == code
    assert "EE-AUD" in iban_keys    # supplier_bank_accounts rowkey == iban


# ── The rebuilt schema: tenant_id FIRST in the PK clause, LAST in column order ───

ALL_REKEYED = AUDITED + UNAUDITED


def test_rebuilt_schema_has_tenant_qualified_pk(on):
    sm, IC, tn, ad = on
    tn.set_tenant("A")
    con = sm.connect()
    try:
        for t in ALL_REKEYED:
            info = con.execute(f"PRAGMA table_info({t})").fetchall()
            pk_by_ord = {r["pk"]: r["name"] for r in info if r["pk"]}
            assert pk_by_ord.get(1) == "tenant_id", (t, pk_by_ord)
            # tenant_id is the LAST column in DEFINITION order -> pks[0] stays natural.
            assert info[-1]["name"] == "tenant_id", t
        for t in AUDITED:
            _cols, pk0 = ad._cols_pk(con, t)
            assert pk0 == NATURAL_PK[t], (t, pk0)
    finally:
        con.close()
    tn.reset_tenant()


# ── Existing-DB preservation: OLD-schema populated suppliers.db, rebuilt ─────────

def _premark_pre_rekey(con, n):
    """Pre-mark the pre-rekey supplier_master migration statements (`n` of them: the
    invoice_cadence ALTER + the P1 tenant_column_ddls, i.e. every statement BEFORE the
    first `__rekey` rebuild) as already applied, so db_migrate.apply() runs ONLY the
    rekey rebuild statements against an already-P1 schema — mirroring an existing
    suppliers.db in the field. (Pre-running the ALTERs would fail on our hand-built P1
    schema with a duplicate-column error.)"""
    con.execute("""CREATE TABLE IF NOT EXISTS _ffs_migrations (
        module TEXT, idx INTEGER, statement TEXT,
        applied_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (module, idx))""")
    for i in range(n):
        con.execute("INSERT OR IGNORE INTO _ffs_migrations(module,idx,statement)"
                    " VALUES ('supplier_master',?,'pre')", (i,))


def _count_pre_rekey_statements(tenancy):
    """Derive (not hardcode) the number of migration statements BEFORE the first
    `__rekey` rebuild, by reconstructing the SAME prefix connect() passes to
    db_migrate.apply (the invoice_cadence ALTER + the P1 tenant_column_ddls)."""
    pre = ["ALTER TABLE suppliers ADD COLUMN invoice_cadence TEXT DEFAULT 'monthly'"]
    pre += list(tenancy.tenant_column_ddls([
        "suppliers", "supplier_bank_accounts", "supplier_vat_registrations",
        "supplier_discounts", "supplier_invoices", "supplier_products",
        "supplier_statements", "statement_invoices",
    ]))
    return len(pre)


def test_rebuild_preserves_rows_on_existing_db(tmp_path, monkeypatch):
    """Build an OLD-schema (natural PKs + P1 tenant_id columns) populated suppliers.db
    across the 7 tables, run the rebuild via connect(), assert per-table row counts
    before==after and every PK tenant-qualified."""
    sm, IC, tn, _ = _fresh_env(tmp_path, monkeypatch, switch=False)

    con = sqlite3.connect(sm.DB)
    # OLD natural-PK schema + the P1 tenant_id column on each (no rekey yet). Note
    # supplier_statements already carries `customer` (added by register_statement's
    # invoice_control migration in the field) — the rebuild must preserve it.
    con.executescript("""
        CREATE TABLE suppliers (
            code TEXT PRIMARY KEY, legal_name TEXT, group_name TEXT,
            address TEXT, home_country TEXT, company_reg TEXT,
            phone TEXT, email TEXT, portal TEXT,
            payment_terms TEXT, payment_notes TEXT, status TEXT DEFAULT 'active', notes TEXT,
            invoice_cadence TEXT DEFAULT 'monthly',
            tenant_id TEXT NOT NULL DEFAULT 'default');
        CREATE TABLE supplier_vat_registrations (
            supplier TEXT, country TEXT, vat_number TEXT, source TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (supplier, country));
        CREATE TABLE supplier_bank_accounts (
            supplier TEXT, beneficiary TEXT, iban TEXT PRIMARY KEY, swift TEXT,
            bank TEXT, currency TEXT, notes TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default');
        CREATE TABLE supplier_products (
            supplier TEXT, product_code TEXT, product_name TEXT, product_group TEXT,
            unit TEXT, vat_rate TEXT, discount_terms TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (supplier, product_code, product_name));
        CREATE TABLE supplier_invoices (
            supplier TEXT, country TEXT, invoice_no TEXT, invoice_date TEXT,
            period TEXT, currency TEXT, gross_total REAL, notes TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (supplier, invoice_no));
        CREATE TABLE supplier_statements (
            supplier TEXT, statement_ref TEXT, period TEXT, statement_date TEXT,
            notes TEXT, customer TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (supplier, statement_ref));
        CREATE TABLE statement_invoices (
            supplier TEXT, statement_ref TEXT, invoice_no TEXT, invoice_date TEXT,
            country TEXT, currency TEXT, net REAL, vat REAL, gross REAL,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (supplier, statement_ref, invoice_no));
    """)
    con.execute("INSERT INTO suppliers (code, legal_name) VALUES ('S1','One')")
    con.execute("INSERT INTO suppliers (code, legal_name) VALUES ('S2','Two')")
    con.execute("INSERT INTO supplier_vat_registrations (supplier, country, vat_number)"
                " VALUES ('S1','France','FR1')")
    con.execute("INSERT INTO supplier_bank_accounts (supplier, iban) VALUES ('S1','EE01')")
    con.execute("INSERT INTO supplier_bank_accounts (supplier, iban) VALUES ('S2','EE02')")
    con.execute("INSERT INTO supplier_products (supplier, product_code, product_name)"
                " VALUES ('S1','27','Diesel')")
    con.execute("INSERT INTO supplier_invoices (supplier, invoice_no, gross_total)"
                " VALUES ('S1','INV1',100)")
    con.execute("INSERT INTO supplier_statements (supplier, statement_ref, customer)"
                " VALUES ('S1','ST1','Cust X')")
    con.execute("INSERT INTO statement_invoices (supplier, statement_ref, invoice_no)"
                " VALUES ('S1','ST1','INV1')")
    _premark_pre_rekey(con, _count_pre_rekey_statements(tn))
    con.commit()
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ALL_REKEYED}
    cust_before = con.execute(
        "SELECT customer FROM supplier_statements WHERE supplier='S1'").fetchone()[0]
    con.close()

    # connect() runs db_migrate.apply -> ONLY the rekey rebuild migrations.
    sm._SCHEMA_READY.clear()
    con = sm.connect()
    try:
        after = {}
        for t in ALL_REKEYED:
            after[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name=? AND type='table'", (t,)
            ).fetchone()[0]
            assert "PRIMARY KEY (tenant_id" in sql, (t, sql)
        cust_after = con.execute(
            "SELECT customer FROM supplier_statements WHERE supplier='S1'").fetchone()[0]
    finally:
        con.close()
    assert before == after
    assert before == {"suppliers": 2, "supplier_vat_registrations": 1,
                      "supplier_bank_accounts": 2, "supplier_products": 1,
                      "supplier_invoices": 1, "supplier_statements": 1,
                      "statement_invoices": 1}
    assert cust_before == cust_after == "Cust X"   # the `customer` column survived


# ── OFF byte-identical: single 'default' tenant; same-key upsert corrects in place ─

def test_switch_off_same_key_corrects_in_place(tmp_path, monkeypatch):
    """With the switch OFF, every write is the single 'default' tenant, so re-setting a
    VAT registration on the same (supplier, country) corrects in place (one row) exactly
    as today — even with a stray thread tenant set."""
    sm, IC, tn, _ = _fresh_env(tmp_path, monkeypatch, switch=False)
    tn.set_tenant("ZZZ")   # inert while OFF
    sm.set_vat_registration("SUP", "France", "FR-OLD")
    sm.set_vat_registration("SUP", "France", "FR-NEW")   # correction, same key
    con = sm.connect()
    try:
        rows = con.execute(
            "SELECT tenant_id, vat_number FROM supplier_vat_registrations "
            "WHERE supplier='SUP' AND country='France'").fetchall()
    finally:
        con.close()
    tn.reset_tenant()
    assert [(r["tenant_id"], r["vat_number"]) for r in rows] == [("default", "FR-NEW")]
