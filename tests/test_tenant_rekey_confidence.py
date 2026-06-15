"""Multi-tenant PK-REKEY — tenant-qualified PRIMARY KEY for confidence.db's
supplier_trust table.

supplier_trust already carries a tenant_id column (P1) and tenant-scoped reads /
stamped writes (P2), but its PRIMARY KEY did NOT include tenant_id, so
record_validation()'s INSERT … ON CONFLICT(supplier, country) resolved on the NATURAL
key only. Under the `multitenant` switch ON, two tenants' trust rows for the SAME
(supplier, country) would COLLIDE/merge — cross-tenant data corruption. This slice
rebuilds supplier_trust with tenant_id FIRST in the PK and rekeys the conflict target.

(validation_events has a surrogate `id INTEGER PRIMARY KEY AUTOINCREMENT` — no
collision risk — and is left untouched. confidence.db is UNAUDITED and supplier_trust
carries no secondary indexes, so there are no triggers/indexes to drop or reinstate.)

What this file proves:
  * NO CROSS-TENANT CLOBBER (the core fix): tenant A and tenant B can each hold the
    SAME (supplier, country) trust row — both persist, each reads back its own.
  * INTRA-TENANT UPSERT still works: another event for the same key as the same tenant
    updates that tenant's own row in place (one row, counters incremented).
  * EXISTING-DB PRESERVATION: an OLD-schema (natural PK + P1 tenant_id) populated
    supplier_trust survives the rebuild with row counts preserved and a tenant-qualified PK.
  * OFF byte-identical: with the switch OFF a single 'default' tenant upserts in place.
  * The REBUILT SCHEMA: tenant_id is in (and first in) the PK.
"""
import importlib
import sqlite3

import pytest


def _fresh_env(tmp_path, monkeypatch, switch):
    """Wire a fresh confidence.db / security.db and set the multitenant switch.
    Returns (confidence, tenancy)."""
    import auth
    import tenancy
    import confidence
    importlib.reload(confidence)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(confidence, "DB", str(tmp_path / "confidence.db"))

    auth.set_setting("multitenant", "1" if switch else "0")
    assert tenancy.multitenant_enabled() is switch
    return confidence, tenancy


@pytest.fixture()
def on(tmp_path, monkeypatch):
    """multitenant switch ON."""
    cf, tn = _fresh_env(tmp_path, monkeypatch, switch=True)
    try:
        yield cf, tn
    finally:
        tn.reset_tenant()


# ── The core fix: NO cross-tenant clobber on the SAME (supplier, country) key ─────

def test_no_cross_tenant_clobber(on):
    """As tenant A record a clean validation for (DKV, LV); as tenant B record a flagged
    one for the SAME (DKV, LV). BOTH supplier_trust rows must persist (distinct
    tenant_id) and each tenant reads back ITS OWN row — pre-rekey the
    ON CONFLICT(supplier, country) would have merged them."""
    cf, tn = on
    tn.set_tenant("A")
    t_a = cf.record_validation("DKV", "LV", True, source="test")
    tn.set_tenant("B")
    t_b = cf.record_validation("DKV", "LV", False, source="test")
    tn.reset_tenant()

    assert t_a > cf.INIT          # A grew (clean)
    assert t_b < cf.INIT          # B decayed (flagged)

    # Owner scope sees BOTH tenants' rows for the shared key.
    tn.set_owner_scope()
    con = cf.connect()
    try:
        rows = sorted((r["tenant_id"], round(r["trust"], 6), r["n_clean"], r["n_flagged"])
                      for r in con.execute(
                          "SELECT tenant_id, trust, n_clean, n_flagged "
                          "FROM supplier_trust WHERE supplier='DKV' AND country='LV'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", round(t_a, 6), 1, 0), ("B", round(t_b, 6), 0, 1)]

    # Each tenant's trust()/scoreboard() reads ITS OWN row only.
    tn.set_tenant("A")
    assert cf.trust("DKV", "LV") == pytest.approx(t_a)
    assert {(r["supplier"], r["country"]): r["n_clean"] for r in cf.scoreboard()} \
        == {("DKV", "LV"): 1}
    tn.set_tenant("B")
    assert cf.trust("DKV", "LV") == pytest.approx(t_b)
    assert {(r["supplier"], r["country"]): r["n_flagged"] for r in cf.scoreboard()} \
        == {("DKV", "LV"): 1}
    tn.reset_tenant()


# ── Intra-tenant upsert still works (ON CONFLICT within a tenant) ─────────────────

def test_intra_tenant_upsert_in_place(on):
    """A second event for the SAME (supplier, country) as the SAME tenant updates that
    tenant's own row in place — one row, counters incremented."""
    cf, tn = on
    tn.set_tenant("A")
    cf.record_validation("DKV", "LV", True, source="test")
    t2 = cf.record_validation("DKV", "LV", True, source="test")
    con = cf.connect()
    try:
        frag, params = tn.scope_clause()
        rows = con.execute(
            "SELECT trust, n_clean, n_flagged FROM supplier_trust "
            "WHERE supplier='DKV' AND country='LV'" + frag, params).fetchall()
    finally:
        con.close()
    tn.reset_tenant()
    assert len(rows) == 1                      # upsert in place, no duplicate row
    assert rows[0]["n_clean"] == 2
    assert rows[0]["n_flagged"] == 0
    assert rows[0]["trust"] == pytest.approx(t2)


# ── The rebuilt schema: tenant_id is (and is first in) the PK ─────────────────────

def test_rebuilt_schema_has_tenant_qualified_pk(on):
    cf, tn = on
    con = cf.connect()
    try:
        info = con.execute("PRAGMA table_info(supplier_trust)").fetchall()
        sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='supplier_trust'").fetchone()[0]
    finally:
        con.close()
    # PK members carry a non-zero `pk` ordinal; tenant_id must be ordinal 1.
    pk_by_ord = {r["pk"]: r["name"] for r in info if r["pk"]}
    assert pk_by_ord.get(1) == "tenant_id", pk_by_ord
    assert pk_by_ord == {1: "tenant_id", 2: "supplier", 3: "country"}, pk_by_ord
    assert "PRIMARY KEY (tenant_id" in sql, sql


# ── OFF regression (byte-identical: single 'default' tenant) ──────────────────────

def test_switch_off_upserts_in_place(tmp_path, monkeypatch):
    """With the switch OFF, every write is the single 'default' tenant, so a second
    event upserts in place (one row) exactly as today — even with a stray thread tenant."""
    cf, tn = _fresh_env(tmp_path, monkeypatch, switch=False)
    tn.set_tenant("ZZZ")   # inert while OFF
    cf.record_validation("DKV", "LV", True, source="test")
    cf.record_validation("DKV", "LV", True, source="test")
    tn.reset_tenant()
    con = cf.connect()
    try:
        rows = con.execute(
            "SELECT tenant_id, n_clean FROM supplier_trust "
            "WHERE supplier='DKV' AND country='LV'").fetchall()
    finally:
        con.close()
    assert [(r["tenant_id"], r["n_clean"]) for r in rows] == [("default", 2)]


# ── Row-count preservation across the rebuild on a PRE-EXISTING populated DB ──────

def _premark_pre_rekey(con, n):
    """Pre-mark the first n confidence _DDL statements (the natural-PK CREATEs + the P1
    tenant_column_ddls ALTERs) as already applied, so db_migrate.apply() runs ONLY the
    rekey rebuild statements against an already-P1 schema — mirroring an existing
    confidence.db in the field."""
    con.execute("""CREATE TABLE IF NOT EXISTS _ffs_migrations (
        module TEXT, idx INTEGER, statement TEXT,
        applied_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (module, idx))""")
    for i in range(n):
        con.execute("INSERT OR IGNORE INTO _ffs_migrations(module,idx,statement)"
                    " VALUES ('confidence',?,'pre')", (i,))


def test_rebuild_preserves_all_rows_on_existing_db(tmp_path, monkeypatch):
    """Build an OLD-schema (natural PK + P1 tenant_id column) confidence.db with rows,
    then let connect()/db_migrate run the rekey rebuild. Row counts must match exactly
    (no data loss) and the PK must become tenant-qualified."""
    cf, tn = _fresh_env(tmp_path, monkeypatch, switch=False)

    # Number of _DDL statements BEFORE the 4-statement rekey block (the natural-PK
    # CREATEs + the P1 tenant_column_ddls ALTERs).
    n_pre = len(cf._DDL) - 4

    con = sqlite3.connect(cf.DB)
    con.execute("""CREATE TABLE supplier_trust (
        supplier TEXT, country TEXT, trust REAL,
        n_clean INTEGER DEFAULT 0, n_flagged INTEGER DEFAULT 0,
        updated_at TEXT, tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (supplier, country))""")
    con.execute("""CREATE TABLE validation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier TEXT, country TEXT, clean INTEGER,
        source TEXT, detail TEXT, created_at TEXT,
        tenant_id TEXT NOT NULL DEFAULT 'default')""")
    con.execute("INSERT INTO supplier_trust VALUES "
                "('DKV','LV',0.6,1,0,'2099-01-01T00:00:00','default')")
    con.execute("INSERT INTO supplier_trust VALUES "
                "('Neste','EE',0.4,0,1,'2099-01-01T00:00:00','default')")
    con.execute("INSERT INTO validation_events "
                "(supplier,country,clean,source,detail,created_at,tenant_id) "
                "VALUES ('DKV','LV',1,'seed','','2099-01-01T00:00:00','default')")
    _premark_pre_rekey(con, n_pre)
    con.commit()
    before = con.execute("SELECT COUNT(*) FROM supplier_trust").fetchone()[0]
    events_before = con.execute("SELECT COUNT(*) FROM validation_events").fetchone()[0]
    con.close()

    # connect() runs db_migrate.apply -> the rekey rebuild migration.
    con = cf.connect()
    try:
        after = con.execute("SELECT COUNT(*) FROM supplier_trust").fetchone()[0]
        events_after = con.execute("SELECT COUNT(*) FROM validation_events").fetchone()[0]
        sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='supplier_trust'").fetchone()[0]
        rows = {(r["supplier"], r["country"]): r["trust"]
                for r in con.execute("SELECT supplier, country, trust FROM supplier_trust")}
    finally:
        con.close()
    assert before == after == 2
    assert events_before == events_after == 1     # validation_events untouched
    assert "PRIMARY KEY (tenant_id" in sql, sql
    assert rows == {("DKV", "LV"): 0.6, ("Neste", "EE"): 0.4}   # values preserved
