"""Multi-tenant PK-REKEY — tenant-qualified PRIMARY KEYs for the AUDITED secrets DB
portal.db (portal_configs / portal_credentials).

THE AUDITED-DB TEMPLATE slice of the composite-PK re-keying program (the benchmark.db
slice — tests/test_tenant_rekey_benchmark.py — was the un-audited template). These two
tables already carry a tenant_id column (P1) and tenant-scoped reads / stamped writes
(P2), but their PRIMARY KEYs did NOT include tenant_id, so set_config/set_credentials'
INSERT … ON CONFLICT(<natural key>) resolved on the NATURAL key only. Under the
`multitenant` switch ON, two tenants writing the SAME supplier/(supplier, entity) would
overwrite each other's stored portal SECRETS — cross-tenant data loss on credentials.
This slice rebuilds each table with tenant_id FIRST in the PK.

What this file proves (in addition to the benchmark template's checks):
  * NO CROSS-TENANT CLOBBER ON SECRETS (the core fix): tenant A and tenant B can each
    hold the SAME (supplier, entity) with DIFFERENT secrets — both rows persist and each
    tenant decrypts its OWN secret. Pre-rekey, B's upsert on the shared natural key would
    have wiped A's credential. Same for set_config on the same supplier.
  * INTRA-TENANT CORRECTION still works: re-set as the SAME tenant updates in place.
  * AUDIT SURVIVES THE REBUILD (the audited-DB proof): after a fresh connect (caches
    cleared so install_audit RUNS like a new process), the aud_portal_configs_i/u/d and
    aud_portal_credentials_i/u/d triggers EXIST on the RENAMED tables; a write produces an
    audit_log row keyed by the PRESERVED natural rowkey (NEW.supplier — NOT tenant_id);
    and the encrypted secret BLOB is NOT in the audit json.
  * ROW/SECRET PRESERVATION across the rebuild on a PRE-EXISTING populated portal.db.
  * The REBUILT SCHEMA: tenant_id in the PK, pks[0] still == supplier (column order).
  * OFF regression (byte-identical: single 'default' tenant).
"""
import importlib
import json
import sqlite3

import pytest


def _fresh_env(tmp_path, monkeypatch, switch):
    """Wire a fresh portal.db / security.db and set the multitenant switch.

    CRITICALLY clears BOTH portal_scraper._SCHEMA_READY AND audit._AUDIT_INSTALLED so
    connect() runs schema + migrations + install_audit exactly like a fresh process —
    the audit triggers are then (re)created against the rekeyed tables. Returns
    (portal_scraper, tenancy, audit)."""
    import auth
    import tenancy
    import audit
    import portal_scraper
    importlib.reload(portal_scraper)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(portal_scraper, "DB", str(tmp_path / "portal.db"))
    portal_scraper._SCHEMA_READY.clear()
    audit._AUDIT_INSTALLED.clear()   # mirror a fresh process: install_audit must RUN
    # isolate the MY-Prices store the scraper would load into (not exercised here)
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    monkeypatch.setattr(pricing_intelligence, "DB", str(tmp_path / "fuel_history.db"))
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))

    auth.set_setting("multitenant", "1" if switch else "0")
    assert tenancy.multitenant_enabled() is switch
    return portal_scraper, tenancy, audit


@pytest.fixture()
def on(tmp_path, monkeypatch):
    """multitenant switch ON."""
    ps, tn, ad = _fresh_env(tmp_path, monkeypatch, switch=True)
    try:
        yield ps, tn, ad
    finally:
        tn.reset_tenant()


# ── The core fix: NO cross-tenant clobber on SECRETS / config ────────────────────

def test_credentials_no_cross_tenant_clobber(on):
    """As tenant A store a credential for (DKV, ENT) with secret S_A; as tenant B store
    a credential for the SAME (DKV, ENT) with a DIFFERENT secret S_B. BOTH rows must
    persist and each tenant must decrypt back its OWN secret. Pre-rekey, B's upsert on
    the shared natural PK (supplier, entity) would have overwritten A's secret."""
    ps, tn, _ = on
    tn.set_tenant("A")
    ps.set_credentials("DKV", "ENT", "user-a", "secret-A")
    tn.set_tenant("B")
    ps.set_credentials("DKV", "ENT", "user-b", "secret-B")
    tn.reset_tenant()

    # Owner scope sees ALL rows — both tenants' versions of the shared key survived.
    tn.set_owner_scope()
    con = ps.connect()
    try:
        tids = sorted(r["tenant_id"] for r in con.execute(
            "SELECT tenant_id FROM portal_credentials WHERE supplier='DKV' AND entity='ENT'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert tids == ["A", "B"]

    # Each tenant reads back (decrypts) its OWN secret for the shared key.
    tn.set_tenant("A")
    assert ps.get_credentials("DKV", "ENT")["secret"] == "secret-A"
    tn.set_tenant("B")
    assert ps.get_credentials("DKV", "ENT")["secret"] == "secret-B"
    tn.reset_tenant()


def test_config_no_cross_tenant_clobber(on):
    """Same shared-key proof for set_config: two tenants configuring the SAME supplier
    keep DISTINCT rows; each reads its own."""
    ps, tn, _ = on
    tn.set_tenant("A")
    ps.set_config("DKV", "demo", base_url="https://a.example")
    tn.set_tenant("B")
    ps.set_config("DKV", "http_json", base_url="https://b.example")
    tn.reset_tenant()

    tn.set_owner_scope()
    con = ps.connect()
    try:
        rows = sorted((r["tenant_id"], r["kind"], r["base_url"]) for r in con.execute(
            "SELECT tenant_id, kind, base_url FROM portal_configs WHERE supplier='DKV'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "demo", "https://a.example"),
                    ("B", "http_json", "https://b.example")]

    tn.set_tenant("A")
    assert ps.get_config("DKV")["base_url"] == "https://a.example"
    tn.set_tenant("B")
    assert ps.get_config("DKV")["base_url"] == "https://b.example"
    tn.reset_tenant()


# ── Intra-tenant correction still works (upsert within a tenant) ─────────────────

def test_intra_tenant_credential_correction_replaces_in_place(on):
    """Re-setting the SAME (supplier, entity) as the SAME tenant corrects that tenant's
    own row in place — one row, new secret (ON CONFLICT on the tenant-qualified PK)."""
    ps, tn, _ = on
    tn.set_tenant("A")
    ps.set_credentials("DKV", "ENT", "user-a", "secret-old")
    ps.set_credentials("DKV", "ENT", "user-a", "secret-new")   # correction
    assert ps.get_credentials("DKV", "ENT")["secret"] == "secret-new"
    con = ps.connect()
    try:
        frag, params = tn.scope_clause()
        n = con.execute(
            "SELECT COUNT(*) FROM portal_credentials WHERE supplier='DKV' AND entity='ENT'"
            + frag, params).fetchone()[0]
    finally:
        con.close()
    tn.reset_tenant()
    assert n == 1   # single corrected row, not a duplicate


# ── AUDIT survives the rebuild (the audited-DB proof) ────────────────────────────

def test_audit_triggers_exist_on_renamed_tables(on):
    """After a fresh connect (caches cleared in the fixture), the audit triggers must
    exist on the RENAMED (rekeyed) tables — DROP TABLE dropped them and the paired
    install_audit recreated them."""
    ps, tn, _ = on
    con = ps.connect()
    try:
        trigs = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
    finally:
        con.close()
    for t in ("portal_configs", "portal_credentials"):
        for sfx in ("i", "u", "d"):
            assert f"aud_{t}_{sfx}" in trigs, (t, sfx, trigs)


def test_audit_rowkey_is_preserved_natural_key_not_tenant(on):
    """A write must produce an audit_log row keyed by the PRESERVED natural rowkey
    (NEW.supplier) — NOT the tenant_id. This is the column-order vs PK-clause-order
    guarantee: tenant_id is FIRST in the PK clause but LAST in the column list, so
    audit._cols_pk returns pks[0] == supplier."""
    ps, tn, ad = on
    tn.set_tenant("A")
    ps.set_credentials("DKV", "ENT", "user-a", "secret-A")
    ps.set_config("DKV", "demo")
    tn.reset_tenant()

    con = ps.connect()
    try:
        # the INSERT triggers logged rowkey == NEW.supplier ('DKV'), never 'A'/'default'
        cred_keys = {r["rowkey"] for r in con.execute(
            "SELECT rowkey FROM audit_log WHERE tbl='portal_credentials' AND action='INSERT'")}
        cfg_keys = {r["rowkey"] for r in con.execute(
            "SELECT rowkey FROM audit_log WHERE tbl='portal_configs' AND action='INSERT'")}
        # pks[0] is supplier by COLUMN order, even though tenant_id leads the PK clause.
        cols, pk = ad._cols_pk(con, "portal_credentials")
        cols_cfg, pk_cfg = ad._cols_pk(con, "portal_configs")
    finally:
        con.close()
    assert cred_keys == {"DKV"}, cred_keys
    assert cfg_keys == {"DKV"}, cfg_keys
    assert pk == "supplier" and pk_cfg == "supplier"
    assert "A" not in cred_keys and "default" not in cred_keys


def test_audit_json_excludes_the_secret_blob(on):
    """The audit new_data json must NOT contain the encrypted secret BLOB (secret_enc /
    extra are BLOB columns — audit-excluded). The crypto blob never enters the log."""
    ps, tn, _ = on
    tn.set_tenant("A")
    ps.set_credentials("DKV", "ENT", "user-a", "top-secret-value",
                       extra={"account": "acct-123"})
    tn.reset_tenant()

    con = ps.connect()
    try:
        row = con.execute(
            "SELECT new_data FROM audit_log WHERE tbl='portal_credentials' "
            "AND action='INSERT'").fetchone()
    finally:
        con.close()
    snap = json.loads(row["new_data"])
    assert "secret_enc" not in snap and "extra" not in snap   # BLOB cols excluded
    assert snap["supplier"] == "DKV" and snap["entity"] == "ENT"
    assert snap["username"] == "user-a"
    assert snap["tenant_id"] == "A"   # tenant_id IS a TEXT column -> audited (not secret)
    # the plaintext secret must never appear anywhere in the snapshot
    assert "top-secret-value" not in row["new_data"]
    assert "acct-123" not in row["new_data"]


def test_audit_logs_update_with_preserved_rowkey(on):
    """An in-place correction (UPDATE via ON CONFLICT) is also audited under the
    preserved natural rowkey."""
    ps, tn, _ = on
    tn.set_tenant("A")
    ps.set_credentials("DKV", "ENT", "user-a", "secret-1")
    ps.set_credentials("DKV", "ENT", "user-a", "secret-2")   # UPDATE path
    tn.reset_tenant()
    con = ps.connect()
    try:
        updates = con.execute(
            "SELECT rowkey FROM audit_log WHERE tbl='portal_credentials' "
            "AND action='UPDATE'").fetchall()
    finally:
        con.close()
    assert updates and all(r["rowkey"] == "DKV" for r in updates)


# ── The rebuilt schema: tenant_id in the PK; pks[0] still == supplier ────────────

def test_rebuilt_schema_has_tenant_qualified_pk(on):
    ps, tn, ad = on
    con = ps.connect()
    try:
        for table, natural in [("portal_configs", "supplier"),
                               ("portal_credentials", "supplier")]:
            info = con.execute(f"PRAGMA table_info({table})").fetchall()
            pk_by_ord = {r["pk"]: r["name"] for r in info if r["pk"]}
            assert pk_by_ord.get(1) == "tenant_id", (table, pk_by_ord)
            assert tn.TENANT_COLUMN in {r["name"] for r in info if r["pk"]}, table
            # tenant_id is the LAST column in DEFINITION order -> pks[0] stays natural.
            assert info[-1]["name"] == "tenant_id", table
            _cols, pk0 = ad._cols_pk(con, table)
            assert pk0 == natural, (table, pk0)
    finally:
        con.close()


# ── Row/secret preservation across the rebuild on a PRE-EXISTING populated DB ────

def _premark_pre_rekey(con):
    """Pre-mark the pre-rekey portal_scraper migration statements (indices 0..3: the
    interval_hours ALTER + the 3 P1 tenant_column_ddls) as already applied, so
    db_migrate.apply() runs ONLY the rekey rebuild statements (index 4 onward) against
    an already-P1 schema — mirroring an existing portal.db in the field."""
    con.execute("""CREATE TABLE IF NOT EXISTS _ffs_migrations (
        module TEXT, idx INTEGER, statement TEXT,
        applied_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (module, idx))""")
    for i in range(4):
        con.execute("INSERT OR IGNORE INTO _ffs_migrations(module,idx,statement)"
                    " VALUES ('portal_scraper',?,'pre')", (i,))


def test_rebuild_preserves_rows_and_secrets_on_existing_db(tmp_path, monkeypatch):
    """Build an OLD-schema (natural PK + P1 tenant_id column) portal.db, store real
    envelope-encrypted credentials via set_credentials BEFORE the rekey, then let
    connect()/db_migrate run the rebuild. Row counts must match exactly and every secret
    must still decrypt — the BLOBs were copied verbatim."""
    ps, tn, _ = _fresh_env(tmp_path, monkeypatch, switch=False)

    # Hand-build the OLD natural-PK + P1-tenant_id schema, then seal real credentials
    # into it through the module crypto so we can prove they still decrypt post-rebuild.
    con = sqlite3.connect(ps.DB)
    con.execute("""CREATE TABLE portal_configs (
        supplier TEXT PRIMARY KEY, kind TEXT, base_url TEXT, config TEXT,
        enabled INTEGER DEFAULT 1, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        interval_hours REAL DEFAULT 0,
        tenant_id TEXT NOT NULL DEFAULT 'default')""")
    con.execute("""CREATE TABLE portal_credentials (
        supplier TEXT, entity TEXT, username TEXT, secret_enc BLOB, extra BLOB,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (supplier, entity))""")
    con.execute("""CREATE TABLE portal_runs (
        id INTEGER PRIMARY KEY, supplier TEXT, entity TEXT,
        started TEXT DEFAULT CURRENT_TIMESTAMP, finished TEXT, status TEXT,
        rows INTEGER DEFAULT 0, message TEXT,
        tenant_id TEXT NOT NULL DEFAULT 'default')""")
    con.execute("INSERT INTO portal_configs (supplier, kind) VALUES ('DKV','demo')")
    con.execute("INSERT INTO portal_configs (supplier, kind) VALUES ('Q8','demo')")
    # seal credentials with the module's envelope crypto (AAD-bound) into the OLD table
    for sup, ent, sec in [("DKV", "ENT1", "sec-dkv"), ("Q8", "ENT2", "sec-q8")]:
        aad = ps._aad(sup, ent)
        con.execute("INSERT INTO portal_credentials (supplier, entity, username, secret_enc)"
                    " VALUES (?,?,?,?)", (sup, ent, "u", ps._encrypt(sec, aad)))
    _premark_pre_rekey(con)
    con.commit()
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("portal_configs", "portal_credentials")}
    con.close()

    # connect() runs db_migrate.apply -> the rekey rebuild migrations.
    ps._SCHEMA_READY.clear()
    con = ps.connect()
    try:
        after = {}
        for t in ("portal_configs", "portal_credentials"):
            after[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0]
            assert "PRIMARY KEY (tenant_id" in sql, (t, sql)
    finally:
        con.close()
    assert before == after == {"portal_configs": 2, "portal_credentials": 2}

    # secrets still decrypt through the public API after the rebuild
    assert ps.get_credentials("DKV", "ENT1")["secret"] == "sec-dkv"
    assert ps.get_credentials("Q8", "ENT2")["secret"] == "sec-q8"


# ── OFF regression (byte-identical: single 'default' tenant) ─────────────────────

def test_switch_off_corrects_in_place(tmp_path, monkeypatch):
    """With the switch OFF, every write is the single 'default' tenant, so re-setting a
    credential corrects in place (one row) exactly as today — even with a stray thread
    tenant set."""
    ps, tn, _ = _fresh_env(tmp_path, monkeypatch, switch=False)
    tn.set_tenant("ZZZ")   # inert while OFF
    ps.set_credentials("DKV", "ENT", "user-a", "secret-old")
    ps.set_credentials("DKV", "ENT", "user-a", "secret-new")
    tn.reset_tenant()
    con = ps.connect()
    try:
        rows = con.execute(
            "SELECT tenant_id FROM portal_credentials WHERE supplier='DKV' AND entity='ENT'"
        ).fetchall()
    finally:
        con.close()
    assert [r["tenant_id"] for r in rows] == ["default"]   # single 'default' row
    assert ps.get_credentials("DKV", "ENT")["secret"] == "secret-new"
