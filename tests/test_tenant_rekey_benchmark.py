"""Multi-tenant PK-REKEY — tenant-qualified PRIMARY KEYs for the benchmark.db price
tables (my_prices / wholesale_prices / advertised_prices).

THE FIRST, TEMPLATE slice of the composite-PK re-keying program. These three tables
already carry a tenant_id column (P1) and tenant-scoped reads / stamped writes (P2),
but their PRIMARY KEYs did NOT include tenant_id, so the `INSERT OR REPLACE` in the
load functions resolved conflicts on the NATURAL key only. Under the `multitenant`
switch ON, two tenants writing the SAME logical key would REPLACE each other —
cross-tenant data loss. This slice rebuilds each table with tenant_id FIRST in the PK.

What this file proves:
  * NO CROSS-TENANT CLOBBER (the core fix): tenant A and tenant B can each hold the
    SAME logical key with DIFFERENT prices — both rows persist. Pre-rekey, B's
    INSERT OR REPLACE on the shared natural key would have wiped A's row.
  * INTRA-TENANT CORRECTION still works: re-loading a key as the SAME tenant corrects
    that tenant's own row in place (OR REPLACE within the tenant) — one row, updated.
  * OFF regression (byte-identical): with the switch OFF, a single 'default' tenant
    means a re-load corrects in place exactly as today.
  * The REBUILT SCHEMA: tenant_id is in the PK and the DROP-destroyed indexes are back.
  * ROW-COUNT PRESERVATION across the rebuild migration on a pre-existing populated DB.
"""
import importlib
import sqlite3

import pytest


PERIOD = "2099-09"


def _fresh_env(tmp_path, monkeypatch, switch):
    """Wire a fresh benchmark.db / security.db and set the multitenant switch.
    Returns (pricing_intelligence, tenancy)."""
    import auth
    import dataproduct
    import tenancy
    import pricing_intelligence
    importlib.reload(pricing_intelligence)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())

    bench = str(tmp_path / "benchmark.db")
    fh = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", bench)
    monkeypatch.setattr(pricing_intelligence, "DB", fh)
    monkeypatch.setattr(pricing_intelligence, "_MIGRATED", set())
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)

    auth.set_setting("multitenant", "1" if switch else "0")
    assert tenancy.multitenant_enabled() is switch
    return pricing_intelligence, tenancy


@pytest.fixture()
def on(tmp_path, monkeypatch):
    """multitenant switch ON."""
    pi, tn = _fresh_env(tmp_path, monkeypatch, switch=True)
    try:
        yield pi, tn
    finally:
        tn.reset_tenant()


# ── The core fix: NO cross-tenant clobber on the SAME logical key ────────────────

def test_my_prices_no_cross_tenant_clobber(on):
    """As tenant A load a my_prices key K; as tenant B load the SAME logical key K with
    a DIFFERENT price. BOTH rows must persist — pre-rekey, B's INSERT OR REPLACE on the
    shared natural PK would have wiped A's row."""
    pi, tn = on
    key = dict(country="LV", city="Riga", date=f"{PERIOD}-01")
    tn.set_tenant("A")
    pi.load_my_prices([{**key, "net_price": 1.40}])
    tn.set_tenant("B")
    pi.load_my_prices([{**key, "net_price": 0.50}])
    tn.reset_tenant()

    # Owner scope sees ALL rows — both tenants' versions of the same key survived.
    tn.set_owner_scope()
    con = pi.connect()
    try:
        rows = sorted((r["tenant_id"], r["net_price"]) for r in con.execute(
            "SELECT tenant_id, net_price FROM my_prices WHERE country='LV'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", 1.40), ("B", 0.50)]

    # Each tenant reads back its OWN price for the shared key (scope_clause).
    tn.set_tenant("A")
    con = pi.connect()
    try:
        price, _ = pi._my_price_lookup(con, "LV", "Riga", f"{PERIOD}-01")
    finally:
        con.close()
    assert price == 1.40
    tn.set_tenant("B")
    con = pi.connect()
    try:
        price, _ = pi._my_price_lookup(con, "LV", "Riga", f"{PERIOD}-01")
    finally:
        con.close()
    assert price == 0.50


def test_advertised_prices_no_cross_tenant_clobber(on):
    """Same shared-key proof for advertised_prices (the historical store)."""
    pi, tn = on
    key = dict(supplier="Q8", country="LV", city="Riga",
               date=f"{PERIOD}-01", product_group="Diesel")
    tn.set_tenant("A")
    pi.load_advertised_prices([{**key, "net_price": 1.55}])
    tn.set_tenant("B")
    pi.load_advertised_prices([{**key, "net_price": 1.20}])
    tn.reset_tenant()

    tn.set_owner_scope()
    con = pi.connect()
    try:
        rows = sorted((r["tenant_id"], r["net_price"]) for r in con.execute(
            "SELECT tenant_id, net_price FROM advertised_prices"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", 1.55), ("B", 1.20)]

    # Each tenant's scoped list shows only its own price for the shared key.
    tn.set_tenant("A")
    a_rows = pi.list_advertised_prices()
    tn.set_tenant("B")
    b_rows = pi.list_advertised_prices()
    tn.reset_tenant()
    assert [r["net_price"] for r in a_rows] == [1.55]
    assert [r["net_price"] for r in b_rows] == [1.20]


def test_wholesale_prices_no_cross_tenant_clobber(on):
    """Same shared-key proof for wholesale_prices."""
    pi, tn = on
    tn.set_tenant("A")
    pi.load_wholesale([{"country": "LV", "date": f"{PERIOD}-01", "net_price": 1.10}])
    tn.set_tenant("B")
    pi.load_wholesale([{"country": "LV", "date": f"{PERIOD}-01", "net_price": 0.90}])
    tn.reset_tenant()

    tn.set_owner_scope()
    con = pi.connect()
    try:
        rows = sorted((r["tenant_id"], r["net_price"]) for r in con.execute(
            "SELECT tenant_id, net_price FROM wholesale_prices"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", 1.10), ("B", 0.90)]


# ── Intra-tenant correction still works (OR REPLACE within a tenant) ─────────────

def test_intra_tenant_correction_replaces_in_place(on):
    """Re-loading the SAME key as the SAME tenant corrects that tenant's own row in
    place — one row, updated price (OR REPLACE on the tenant-qualified PK)."""
    pi, tn = on
    key = dict(country="LV", city="Riga", date=f"{PERIOD}-01")
    tn.set_tenant("A")
    pi.load_my_prices([{**key, "net_price": 1.40}])
    pi.load_my_prices([{**key, "net_price": 1.45}])   # correction
    con = pi.connect()
    try:
        frag, params = tn.scope_clause()
        got = con.execute(
            "SELECT net_price FROM my_prices WHERE country='LV'" + frag,
            params).fetchall()
    finally:
        con.close()
    tn.reset_tenant()
    assert [r["net_price"] for r in got] == [1.45]   # single row, corrected


def test_intra_tenant_advertised_correction_replaces_in_place(on):
    pi, tn = on
    key = dict(supplier="Q8", country="LV", city="Riga",
               date=f"{PERIOD}-01", product_group="Diesel")
    tn.set_tenant("A")
    pi.load_advertised_prices([{**key, "net_price": 1.55}])
    pi.load_advertised_prices([{**key, "net_price": 1.60}])
    rows = pi.list_advertised_prices()
    tn.reset_tenant()
    assert [r["net_price"] for r in rows] == [1.60]   # single corrected row


# ── OFF regression (byte-identical: single 'default' tenant) ─────────────────────

def test_switch_off_corrects_in_place(tmp_path, monkeypatch):
    """With the switch OFF, every write is the single 'default' tenant, so re-loading a
    key corrects in place (one row) exactly as today — even with a stray thread tenant."""
    pi, tn = _fresh_env(tmp_path, monkeypatch, switch=False)
    key = dict(country="LV", city="Riga", date=f"{PERIOD}-01")
    tn.set_tenant("ZZZ")   # inert while OFF
    pi.load_my_prices([{**key, "net_price": 1.40}])
    pi.load_my_prices([{**key, "net_price": 1.45}])
    tn.reset_tenant()
    con = pi.connect()
    try:
        rows = con.execute(
            "SELECT tenant_id, net_price FROM my_prices WHERE country='LV'").fetchall()
    finally:
        con.close()
    assert [(r["tenant_id"], r["net_price"]) for r in rows] == [("default", 1.45)]


# ── The rebuilt schema: tenant_id in the PK + indexes restored ───────────────────

def test_rebuilt_schema_has_tenant_qualified_pk_and_indexes(on):
    pi, tn = on
    con = pi.connect()
    try:
        for table, expect_first_pk in [
                ("my_prices", "tenant_id"),
                ("wholesale_prices", "tenant_id"),
                ("advertised_prices", "tenant_id")]:
            info = con.execute(f"PRAGMA table_info({table})").fetchall()
            # PK members carry a non-zero `pk` ordinal; tenant_id must be ordinal 1.
            pk_by_ord = {r["pk"]: r["name"] for r in info if r["pk"]}
            assert pk_by_ord.get(1) == expect_first_pk, (table, pk_by_ord)
            assert tn.TENANT_COLUMN in {r["name"] for r in info if r["pk"]}, table
        # the DROP-destroyed indexes were recreated after the rename.
        names = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"ix_myp", "ix_whp", "ix_advp"} <= names, names
    finally:
        con.close()


# ── Row-count preservation across the rebuild on a PRE-EXISTING populated DB ─────

def _premark_p1(con):
    """Pre-mark the 9 pre-rekey _BENCHMARK_DDL statements (indices 0..8: the natural-PK
    CREATEs/INDEXes + the P1 tenant_column_ddls ALTERs) as already applied, so a
    db_migrate.apply() runs ONLY the rekey rebuild statements (index 9 onward) against
    an already-P1 schema — mirroring an existing benchmark.db in the field."""
    con.execute("""CREATE TABLE IF NOT EXISTS _ffs_migrations (
        module TEXT, idx INTEGER, statement TEXT,
        applied_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (module, idx))""")
    for i in range(9):
        con.execute("INSERT OR IGNORE INTO _ffs_migrations(module,idx,statement)"
                    " VALUES ('pricing_intelligence',?,'pre')", (i,))


def test_rebuild_preserves_all_rows_on_existing_db(tmp_path, monkeypatch):
    """Build an OLD-schema (natural PK + P1 tenant_id column) benchmark.db with rows,
    then let connect()/db_migrate run the rekey rebuild. Row counts must match exactly
    (no data loss) and every PK must become tenant-qualified."""
    pi, tn = _fresh_env(tmp_path, monkeypatch, switch=False)
    bench = pi.BENCHMARK_DB

    con = sqlite3.connect(bench)
    con.execute("""CREATE TABLE my_prices (country TEXT, city TEXT, date TEXT,
        product_group TEXT DEFAULT 'Diesel', net_price REAL, source TEXT DEFAULT 'upload',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (country, city, date, product_group))""")
    con.execute("""CREATE TABLE wholesale_prices (country TEXT, date TEXT,
        product_group TEXT DEFAULT 'Diesel', net_price REAL, source TEXT,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (country, date, product_group))""")
    con.execute("""CREATE TABLE advertised_prices (supplier TEXT, country TEXT, city TEXT,
        date TEXT, product_group TEXT DEFAULT 'Diesel', net_price REAL,
        source TEXT DEFAULT 'upload', tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (supplier, country, city, date, product_group))""")
    con.execute("CREATE INDEX ix_myp ON my_prices(country, city, date)")
    con.execute("CREATE INDEX ix_whp ON wholesale_prices(country, date)")
    con.execute("CREATE INDEX ix_advp ON advertised_prices(supplier, country, city, date)")
    con.execute("INSERT INTO my_prices VALUES ('LV','Riga','2099-01-01','Diesel',1.4,'upload','default')")
    con.execute("INSERT INTO my_prices VALUES ('LT','Vilnius','2099-01-01','Diesel',1.3,'upload','default')")
    con.execute("INSERT INTO wholesale_prices VALUES ('LV','2099-01-01','Diesel',1.1,'index','default')")
    con.execute("INSERT INTO advertised_prices VALUES ('Q8','LV','Riga','2099-01-01','Diesel',1.5,'upload','default')")
    _premark_p1(con)
    con.commit()
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("my_prices", "wholesale_prices", "advertised_prices")}
    con.close()

    # connect() runs db_migrate.apply -> the rekey rebuild migrations.
    pi._MIGRATED = set()
    con = pi.connect()
    try:
        after = {}
        for t in ("my_prices", "wholesale_prices", "advertised_prices"):
            after[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0]
            assert "PRIMARY KEY (tenant_id" in sql, (t, sql)
    finally:
        con.close()
    assert before == after == {"my_prices": 2, "wholesale_prices": 1, "advertised_prices": 1}
