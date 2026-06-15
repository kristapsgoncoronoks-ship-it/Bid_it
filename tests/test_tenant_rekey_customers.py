"""Multi-tenant PK-REKEY — tenant-qualified PRIMARY KEYs for the AUDITED CRM DB
customers.db (the BIGGEST rekey slice: 7 audited natural-key tables).

This follows the two already-approved templates: the benchmark.db (un-audited) slice
and the portal.db (AUDITED) slice (tests/test_tenant_rekey_portal.py). The CRM
natural-key tables already carry a tenant_id column (P1) and tenant-scoped reads /
stamped writes (P2), but their PRIMARY KEYs did NOT include tenant_id, so the seeders'
INSERT OR REPLACE and the setters' INSERT … ON CONFLICT(<natural key>) resolved on the
NATURAL key only. Under the `multitenant` switch ON, two tenants writing the same
code / iban / (customer, …) / key would COLLIDE and overwrite each other's CRM rows —
cross-tenant data loss. customers' inline `company_name UNIQUE` was likewise GLOBAL.
This slice rebuilds each of the 7 tables with tenant_id FIRST in the PK (LAST in the
column list) and turns customers' inline UNIQUE into a composite UNIQUE(tenant_id,
company_name).

The 7 rebuilt tables and their preserved natural audit rowkey (pks[0]):
    customers                  PK (code)              -> (tenant_id, code)        rowkey code
    customer_bank_accounts     PK (iban)              -> (tenant_id, iban)        rowkey iban
    customer_supplier_accounts PK (customer,supplier) -> (tenant_id,customer,supplier) rowkey customer
    customer_fees              PK (customer,country)  -> (tenant_id,customer,country)  rowkey customer
    customer_countries         PK (customer,country)  -> (tenant_id,customer,country)  rowkey customer
    country_requirements       PK (country,kind)      -> (tenant_id,country,kind)      rowkey country
    checklist_rules            PK (key)               -> (tenant_id,key)          rowkey key

What this file proves:
  * NO CROSS-TENANT CLOBBER, driving the REAL public functions (add_customer, the
    fee/country/checklist setters) for customers / customer_fees / customer_countries /
    checklist_rules: A and B can hold the SAME key and each reads its OWN.
  * COMPOSITE company-name UNIQUE: A and B can BOTH name a customer the same; WITHIN one
    tenant the company_name UNIQUE still rejects a duplicate.
  * AUDIT SURVIVES THE REBUILD on ALL 7 renamed tables: the aud_<t>_i/u/d triggers exist,
    and a write logs an audit_log row keyed by the PRESERVED natural rowkey (NOT tenant_id).
  * EXISTING-DB PRESERVATION: an OLD-schema (natural PKs + P1 tenant_id) populated
    customers.db rebuilt via connect() keeps per-table row counts and tenant-qualifies
    every PK.
  * OFF byte-identical: the single 'default' tenant; a same-key upsert corrects in place.
"""
import importlib
import json
import sqlite3

import pytest


def _fresh_env(tmp_path, monkeypatch, switch):
    """Wire a fresh customers.db / security.db and set the multitenant switch.

    CRITICALLY clears BOTH customer_master._SCHEMA_READY AND audit._AUDIT_INSTALLED so
    connect() runs schema + migrations + install_audit exactly like a fresh process —
    the audit triggers are then (re)created against the rekeyed tables. Returns
    (customer_master, tenancy, audit)."""
    import auth
    import tenancy
    import audit
    import customer_master
    importlib.reload(customer_master)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "customers.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "docs"))
    audit._AUDIT_INSTALLED.clear()   # mirror a fresh process: install_audit must RUN

    auth.set_setting("multitenant", "1" if switch else "0")
    assert tenancy.multitenant_enabled() is switch
    return customer_master, tenancy, audit


@pytest.fixture()
def on(tmp_path, monkeypatch):
    """multitenant switch ON."""
    cm, tn, ad = _fresh_env(tmp_path, monkeypatch, switch=True)
    try:
        yield cm, tn, ad
    finally:
        tn.reset_tenant()


# ── The core fix: NO cross-tenant clobber (driving the REAL public functions) ────

def test_customers_no_cross_tenant_clobber(on):
    """add_customer with the SAME code 'SHARED' as tenant A and tenant B — both rows
    persist (the tenant-qualified PK), each tenant reads its OWN."""
    cm, tn, _ = on
    tn.set_tenant("A")
    cm.add_customer("SHARED", "Acme A SIA", country="LV")
    tn.set_tenant("B")
    cm.add_customer("SHARED", "Beta B UAB", country="LT")
    tn.reset_tenant()

    tn.set_owner_scope()
    con = cm.connect()
    try:
        rows = sorted((r["tenant_id"], r["company_name"]) for r in con.execute(
            "SELECT tenant_id, company_name FROM customers WHERE code='SHARED'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "Acme A SIA"), ("B", "Beta B UAB")]

    tn.set_tenant("A")
    assert cm.get_customer("SHARED")["company_name"] == "Acme A SIA"
    tn.set_tenant("B")
    assert cm.get_customer("SHARED")["company_name"] == "Beta B UAB"
    tn.reset_tenant()


def test_customer_fees_no_cross_tenant_clobber(on):
    """set_country_fee on the SAME (customer, country) as A and B — both persist."""
    cm, tn, _ = on
    tn.set_tenant("A")
    cm.add_customer("CUST", "Co A SIA", country="LV")
    con = cm.connect()
    try:
        cm.set_country_fee(con, "CUST", "DE", 5.0, 100.0)
    finally:
        con.close()
    tn.set_tenant("B")
    cm.add_customer("CUST", "Co B UAB", country="LT")
    con = cm.connect()
    try:
        cm.set_country_fee(con, "CUST", "DE", 9.0, 250.0)
    finally:
        con.close()
    tn.reset_tenant()

    tn.set_owner_scope()
    con = cm.connect()
    try:
        rows = sorted((r["tenant_id"], r["fee_pct"], r["fee_min"]) for r in con.execute(
            "SELECT tenant_id, fee_pct, fee_min FROM customer_fees "
            "WHERE customer='CUST' AND country='DE'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", 5.0, 100.0), ("B", 9.0, 250.0)]


def test_customer_countries_no_cross_tenant_clobber(on):
    """request_country / activate_country on the SAME (customer, country) as A and B."""
    cm, tn, _ = on
    tn.set_tenant("A")
    cm.add_customer("CUST", "Co A SIA", country="LV")
    con = cm.connect()
    try:
        cm.request_country(con, "CUST", "DE")
    finally:
        con.close()
    tn.set_tenant("B")
    cm.add_customer("CUST", "Co B UAB", country="LT")
    con = cm.connect()
    try:
        cm.request_country(con, "CUST", "DE")
        cm.activate_country(con, "CUST", "DE", True)
    finally:
        con.close()
    tn.reset_tenant()

    tn.set_owner_scope()
    con = cm.connect()
    try:
        rows = sorted((r["tenant_id"], r["status"]) for r in con.execute(
            "SELECT tenant_id, status FROM customer_countries "
            "WHERE customer='CUST' AND country='DE'"))
    finally:
        con.close()
    tn.reset_tenant()
    # A stayed 'requested'; B was activated — neither clobbered the other.
    assert rows == [("A", "requested"), ("B", "active")]


def test_checklist_rules_no_cross_tenant_clobber(on):
    """set_checklist_rule on the SAME key as A and B — both persist with own label."""
    cm, tn, _ = on
    tn.set_tenant("A")
    con = cm.connect()
    try:
        cm.set_checklist_rule(con, "myrule", "Label A")
    finally:
        con.close()
    tn.set_tenant("B")
    con = cm.connect()
    try:
        cm.set_checklist_rule(con, "myrule", "Label B")
    finally:
        con.close()
    tn.reset_tenant()

    tn.set_owner_scope()
    con = cm.connect()
    try:
        rows = sorted((r["tenant_id"], r["label"]) for r in con.execute(
            "SELECT tenant_id, label FROM checklist_rules WHERE key='myrule'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "Label A"), ("B", "Label B")]


# ── Composite company-name UNIQUE ────────────────────────────────────────────────

def test_company_name_unique_is_per_tenant(on):
    """Tenant A and tenant B can BOTH have a customer with the SAME company_name
    (different code) — the pre-rekey GLOBAL UNIQUE(company_name) would reject B."""
    cm, tn, _ = on
    tn.set_tenant("A")
    cm.add_customer("ACODE", "Shared Name OU", country="EE")
    tn.set_tenant("B")
    cm.add_customer("BCODE", "Shared Name OU", country="LT")   # same name, OK now
    tn.reset_tenant()

    tn.set_owner_scope()
    con = cm.connect()
    try:
        rows = sorted((r["tenant_id"], r["code"]) for r in con.execute(
            "SELECT tenant_id, code FROM customers WHERE company_name='Shared Name OU'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "ACODE"), ("B", "BCODE")]


def test_company_name_unique_still_holds_within_a_tenant(on):
    """WITHIN one tenant the company_name UNIQUE still rejects a duplicate name."""
    cm, tn, _ = on
    tn.set_tenant("A")
    cm.add_customer("FIRST", "Same Name SIA", country="LV")
    with pytest.raises(sqlite3.IntegrityError):
        cm.add_customer("SECOND", "Same Name SIA", country="LV")   # dup name, same tenant
    tn.reset_tenant()


# ── AUDIT survives the rebuild on ALL 7 renamed tables ───────────────────────────

ALL_REKEYED = [
    "customers", "customer_bank_accounts", "customer_supplier_accounts",
    "customer_fees", "customer_countries", "country_requirements", "checklist_rules",
]
# natural audit rowkey (pks[0]) preserved per table
NATURAL_PK = {
    "customers": "code", "customer_bank_accounts": "iban",
    "customer_supplier_accounts": "customer", "customer_fees": "customer",
    "customer_countries": "customer", "country_requirements": "country",
    "checklist_rules": "key",
}


def test_audit_triggers_exist_on_all_renamed_tables(on):
    """After a fresh connect (caches cleared in the fixture), aud_<t>_i/u/d must exist
    on every RENAMED rekeyed table — DROP TABLE dropped them, install_audit recreated."""
    cm, tn, _ = on
    tn.set_tenant("A")   # bind a tenant: the first connect seeds checklist defaults (a write)
    con = cm.connect()
    try:
        trigs = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
    finally:
        con.close()
    for t in ALL_REKEYED:
        for sfx in ("i", "u", "d"):
            assert f"aud_{t}_{sfx}" in trigs, (t, sfx)


def test_audit_rowkey_is_preserved_natural_key_not_tenant(on):
    """A write to each rekeyed table logs an audit_log row keyed by the PRESERVED
    natural rowkey (e.g. customers->code, customer_fees->customer) — NOT tenant_id /
    'default'. This is the column-order vs PK-clause-order guarantee (tenant_id FIRST in
    the PK clause but LAST in the column list, so audit._cols_pk pks[0] == natural)."""
    cm, tn, ad = on
    tn.set_tenant("A")
    cm.add_customer("AUDCO", "Audit Co SIA", country="LV")
    con = cm.connect()
    try:
        cm.set_country_fee(con, "AUDCO", "DE", 3.0, 50.0)        # customer_fees
        cm.request_country(con, "AUDCO", "DE")                   # customer_countries
        cm.set_checklist_rule(con, "audrule", "Audit Rule")      # checklist_rules
        cm.set_country_requirements(con, "DE", ["power_of_attorney"])  # country_requirements
        con.execute("""INSERT INTO customer_bank_accounts
            (customer, iban, swift, bank, currency, tenant_id)
            VALUES ('AUDCO','EE001','SW','Bank','EUR',?)""", (tn.write_tenant(),))
        con.execute("""INSERT INTO customer_supplier_accounts
            (customer, supplier, account_no, tenant_id)
            VALUES ('AUDCO','DKV','42',?)""", (tn.write_tenant(),))
        con.commit()
    finally:
        con.close()
    tn.reset_tenant()

    con = cm.connect()
    try:
        for t in ALL_REKEYED:
            natural = NATURAL_PK[t]
            # the audit trigger logs NEW.{natural}, never tenant_id/'default'
            keys = {r["rowkey"] for r in con.execute(
                "SELECT rowkey FROM audit_log WHERE tbl=? AND action='INSERT'", (t,))}
            assert keys, t
            assert "A" not in keys and "default" not in keys, (t, keys)
            cols, pk0 = ad._cols_pk(con, t)
            assert pk0 == natural, (t, pk0)
    finally:
        con.close()

    # spot-check the exact preserved key value on two representative tables
    con = cm.connect()
    try:
        cust_keys = {r["rowkey"] for r in con.execute(
            "SELECT rowkey FROM audit_log WHERE tbl='customers' AND action='INSERT'")}
        fee_keys = {r["rowkey"] for r in con.execute(
            "SELECT rowkey FROM audit_log WHERE tbl='customer_fees' AND action='INSERT'")}
    finally:
        con.close()
    assert "AUDCO" in cust_keys     # customers rowkey == code
    assert "AUDCO" in fee_keys      # customer_fees rowkey == customer


def test_audit_json_carries_tenant_id_column(on):
    """tenant_id is a TEXT column -> audited; the new_data snapshot includes it."""
    cm, tn, _ = on
    tn.set_tenant("A")
    cm.add_customer("JCO", "Json Co SIA", country="LV")
    tn.reset_tenant()
    con = cm.connect()
    try:
        row = con.execute(
            "SELECT new_data FROM audit_log WHERE tbl='customers' "
            "AND action='INSERT' AND rowkey='JCO'").fetchone()
    finally:
        con.close()
    snap = json.loads(row["new_data"])
    assert snap["code"] == "JCO"
    assert snap["tenant_id"] == "A"


# ── The rebuilt schema: tenant_id FIRST in the PK clause, LAST in column order ───

def test_rebuilt_schema_has_tenant_qualified_pk(on):
    cm, tn, ad = on
    tn.set_tenant("A")   # bind a tenant: the first connect seeds checklist defaults (a write)
    con = cm.connect()
    try:
        for t in ALL_REKEYED:
            info = con.execute(f"PRAGMA table_info({t})").fetchall()
            pk_by_ord = {r["pk"]: r["name"] for r in info if r["pk"]}
            assert pk_by_ord.get(1) == "tenant_id", (t, pk_by_ord)
            assert tn.TENANT_COLUMN in {r["name"] for r in info if r["pk"]}, t
            # tenant_id is the LAST column in DEFINITION order -> pks[0] stays natural.
            assert info[-1]["name"] == "tenant_id", t
            _cols, pk0 = ad._cols_pk(con, t)
            assert pk0 == NATURAL_PK[t], (t, pk0)
    finally:
        con.close()


def test_customers_has_composite_company_name_unique(on):
    """The inline company_name UNIQUE became a TABLE-level UNIQUE(tenant_id,
    company_name) — exactly one such index, over the two columns in order."""
    cm, tn, _ = on
    tn.set_tenant("A")   # bind a tenant: the first connect seeds checklist defaults (a write)
    con = cm.connect()
    try:
        idx_list = con.execute("PRAGMA index_list(customers)").fetchall()
        uniques = [r["name"] for r in idx_list if r["unique"]]
        found = None
        for name in uniques:
            cols = [r["name"] for r in con.execute(f"PRAGMA index_info({name})")]
            if cols == ["tenant_id", "company_name"]:
                found = name
        assert found is not None, uniques
    finally:
        con.close()


# ── Existing-DB preservation: OLD-schema populated customers.db, rebuilt ─────────

def _premark_pre_rekey(con, n):
    """Pre-mark the pre-rekey customer_master migration statements (`n` of them: the
    ALTERs + the P1 tenant_column_ddls, i.e. every statement BEFORE the first `__rekey`
    rebuild) as already applied, so db_migrate.apply() runs ONLY the rekey rebuild
    statements against an already-P1 schema — mirroring an existing customers.db in the
    field. (Pre-running the ALTERs/tenant_column_ddls would fail on our hand-built P1
    schema with a duplicate-column error.)"""
    con.execute("""CREATE TABLE IF NOT EXISTS _ffs_migrations (
        module TEXT, idx INTEGER, statement TEXT,
        applied_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (module, idx))""")
    for i in range(n):
        con.execute("INSERT OR IGNORE INTO _ffs_migrations(module,idx,statement)"
                    " VALUES ('customer_master',?,'pre')", (i,))


def _count_pre_rekey_statements(cm, tenancy):
    """Derive (not hardcode) the number of migration statements BEFORE the first
    `__rekey` rebuild, by reconstructing the SAME list connect() passes to
    db_migrate.apply (the ALTERs + the P1 tenant_column_ddls). Robust to the exact
    count so the existing-DB test never drifts from the module."""
    pre = [
        "ALTER TABLE customers ADD COLUMN fee_pct REAL DEFAULT 0",
        "ALTER TABLE customers ADD COLUMN fee_min REAL DEFAULT 0",
        "ALTER TABLE customers ADD COLUMN payout_route TEXT DEFAULT 'customer'",
        "ALTER TABLE customer_documents ADD COLUMN country TEXT",
        "ALTER TABLE customers ADD COLUMN nace_code TEXT",
        "ALTER TABLE customer_documents ADD COLUMN valid_until TEXT",
        "ALTER TABLE customers ADD COLUMN signatory_name TEXT",
        "ALTER TABLE customers ADD COLUMN signatory_title TEXT",
    ]
    pre += list(tenancy.tenant_column_ddls([
        "customers", "customer_bank_accounts", "customer_supplier_accounts",
        "customer_documents", "customer_fees", "customer_countries",
        "country_requirements", "checklist_rules", "doc_templates",
        "document_requests",
    ]))
    return len(pre)


def test_rebuild_preserves_rows_on_existing_db(tmp_path, monkeypatch):
    """Build an OLD-schema (natural PKs + P1 tenant_id columns) populated customers.db
    across the 7 tables, run the rebuild via connect(), assert per-table row counts
    before==after and every PK tenant-qualified."""
    cm, tn, _ = _fresh_env(tmp_path, monkeypatch, switch=False)

    con = sqlite3.connect(cm.DB)
    # OLD natural-PK schema + the P1 tenant_id column on each (no rekey yet).
    con.executescript("""
        CREATE TABLE customers (
            code TEXT PRIMARY KEY, company_name TEXT UNIQUE,
            reg_number TEXT, vat_number TEXT, legal_address TEXT, country TEXT,
            home_portal TEXT, phone TEXT, email TEXT,
            status TEXT DEFAULT 'active', notes TEXT,
            fee_pct REAL DEFAULT 0, fee_min REAL DEFAULT 0,
            payout_route TEXT DEFAULT 'customer',
            nace_code TEXT, signatory_name TEXT, signatory_title TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default');
        CREATE TABLE customer_bank_accounts (
            customer TEXT, iban TEXT PRIMARY KEY, swift TEXT, bank TEXT,
            currency TEXT, purpose TEXT DEFAULT 'refund payout', notes TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default');
        CREATE TABLE customer_supplier_accounts (
            customer TEXT, supplier TEXT, account_no TEXT, notes TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (customer, supplier));
        CREATE TABLE customer_fees (
            customer TEXT, country TEXT, fee_pct REAL DEFAULT 0, fee_min REAL DEFAULT 0,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (customer, country));
        CREATE TABLE customer_countries (
            customer TEXT, country TEXT, status TEXT DEFAULT 'pending',
            requested_at TEXT, activated_at TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (customer, country));
        CREATE TABLE country_requirements (
            country TEXT, kind TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (country, kind));
        CREATE TABLE checklist_rules (
            key TEXT PRIMARY KEY, label TEXT, scope TEXT DEFAULT 'customer',
            check_type TEXT DEFAULT 'document', ref TEXT,
            active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0,
            tenant_id TEXT NOT NULL DEFAULT 'default');
    """)
    con.execute("INSERT INTO customers (code, company_name) VALUES ('C1','One SIA')")
    con.execute("INSERT INTO customers (code, company_name) VALUES ('C2','Two UAB')")
    con.execute("INSERT INTO customer_bank_accounts (customer, iban) VALUES ('C1','EE01')")
    con.execute("INSERT INTO customer_bank_accounts (customer, iban) VALUES ('C2','EE02')")
    con.execute("INSERT INTO customer_supplier_accounts (customer, supplier, account_no)"
                " VALUES ('C1','DKV','11')")
    con.execute("INSERT INTO customer_fees (customer, country, fee_pct) VALUES ('C1','DE',5)")
    con.execute("INSERT INTO customer_countries (customer, country, status)"
                " VALUES ('C1','DE','active')")
    con.execute("INSERT INTO country_requirements (country, kind)"
                " VALUES ('DE','power_of_attorney')")
    con.execute("INSERT INTO checklist_rules (key, label) VALUES ('contract','Contract')")
    _premark_pre_rekey(con, _count_pre_rekey_statements(cm, tn))
    con.commit()
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ALL_REKEYED}
    con.close()

    # connect() runs db_migrate.apply -> ONLY the rekey rebuild migrations.
    cm._SCHEMA_READY.clear()
    con = cm.connect()
    try:
        after = {}
        for t in ALL_REKEYED:
            after[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name=? AND type='table'", (t,)
            ).fetchone()[0]
            assert "PRIMARY KEY (tenant_id" in sql, (t, sql)
    finally:
        con.close()
    assert before == after
    assert before == {"customers": 2, "customer_bank_accounts": 2,
                      "customer_supplier_accounts": 1, "customer_fees": 1,
                      "customer_countries": 1, "country_requirements": 1,
                      "checklist_rules": 1}


# ── OFF byte-identical: single 'default' tenant; same-key upsert corrects in place ─

def test_switch_off_same_key_corrects_in_place(tmp_path, monkeypatch):
    """With the switch OFF, every write is the single 'default' tenant, so re-setting a
    checklist rule / fee on the same key corrects in place (one row) exactly as today —
    even with a stray thread tenant set."""
    cm, tn, _ = _fresh_env(tmp_path, monkeypatch, switch=False)
    tn.set_tenant("ZZZ")   # inert while OFF
    con = cm.connect()
    try:
        cm.set_checklist_rule(con, "k1", "Old Label")
        cm.set_checklist_rule(con, "k1", "New Label")   # correction, same key
        rows = con.execute(
            "SELECT tenant_id, label FROM checklist_rules WHERE key='k1'").fetchall()
    finally:
        con.close()
    tn.reset_tenant()
    assert [(r["tenant_id"], r["label"]) for r in rows] == [("default", "New Label")]
