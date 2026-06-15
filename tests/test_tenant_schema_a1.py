"""Multi-tenancy P1 — schema plumbing (slice A1: vat_claims.db, benchmark.db, portal.db).

Extends the proven CRM slice (test_tenant_schema.py) to three more APP-OWNED DBs.
Same cardinal invariant: adding a `tenant_id TEXT NOT NULL DEFAULT 'default'` column
that NO query reads/filters changes no behavior. The `multitenant` switch stays OFF
and scope_clause is NOT wired into any query (that's P2). Existing + new rows backfill
to the default tenant via the column DEFAULT.

Each section: (a) every named tenant table carries the tenant_id column with the
default; (b) an INSERT that doesn't name tenant_id backfills 'default'; (c) the two
SELECT*-exposure fixes keep their column contract free of tenant_id; (d) a second
connect on the SAME file is idempotent (the ALTER runs once).
"""
import importlib

import pytest


# ── vat_refund.py → vat_claims.db ──────────────────────────────────────────────

VAT_TABLES = [
    "vat_applications", "vat_claimed_invoices", "invoice_documents",
    "vat_invoice_waivers", "note_invoice_overrides",
]


def _fresh_vr(tmp_path, monkeypatch):
    import vat_refund
    importlib.reload(vat_refund)
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    # keep _migrate_from_analytics a no-op (no real demo DB to seed from).
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "fh.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    return vat_refund


def _assert_tenant_col(con, table):
    cols = {r["name"]: r for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    assert "tenant_id" in cols, f"{table} missing tenant_id"
    col = cols["tenant_id"]
    assert col["type"] == "TEXT", f"{table}.tenant_id type {col['type']!r}"
    assert col["notnull"] == 1, f"{table}.tenant_id should be NOT NULL"
    assert col["dflt_value"] == "'default'", \
        f"{table}.tenant_id default {col['dflt_value']!r}"


def test_vat_claims_tables_have_tenant_id_default(tmp_path, monkeypatch):
    vr = _fresh_vr(tmp_path, monkeypatch)
    con = vr.connect()
    try:
        for table in VAT_TABLES:
            _assert_tenant_col(con, table)
    finally:
        con.close()


def test_vat_claims_default_backfills_unspecified_insert(tmp_path, monkeypatch):
    """A representative INSERT that does NOT name tenant_id yields 'default'."""
    vr = _fresh_vr(tmp_path, monkeypatch)
    con = vr.connect()
    try:
        con.execute("""INSERT INTO vat_applications (entity, refund_country, ref_period)
                       VALUES ('E', 'Belgium', '2026-Q1')""")
        con.commit()
        assert con.execute(
            "SELECT tenant_id FROM vat_applications").fetchone()[0] == "default"
    finally:
        con.close()


def test_vat_claims_idempotent_second_connect(tmp_path, monkeypatch):
    vr = _fresh_vr(tmp_path, monkeypatch)
    vr.connect().close()
    monkeypatch.setattr(vr, "_SCHEMA_READY", set())
    con = vr.connect()   # must NOT raise (db_migrate skips the applied ALTERs)
    try:
        cols = [r["name"] for r in
                con.execute("PRAGMA table_info(vat_applications)").fetchall()]
        assert cols.count("tenant_id") == 1
    finally:
        con.close()


# ── pricing_intelligence.py → benchmark.db ─────────────────────────────────────

BENCHMARK_TABLES = ["my_prices", "wholesale_prices"]


def _fresh_pi(tmp_path, monkeypatch):
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "bench.db"))
    # point the product DB at a missing file so _migrate_from_product is a no-op.
    monkeypatch.setattr(pricing_intelligence, "DB", str(tmp_path / "fh.db"))
    monkeypatch.setattr(pricing_intelligence, "_MIGRATED", set())
    return pricing_intelligence


def test_benchmark_tables_have_tenant_id_default(tmp_path, monkeypatch):
    pi = _fresh_pi(tmp_path, monkeypatch)
    con = pi.connect()
    try:
        for table in BENCHMARK_TABLES:
            _assert_tenant_col(con, table)
    finally:
        con.close()


def test_benchmark_default_backfills_unspecified_insert(tmp_path, monkeypatch):
    pi = _fresh_pi(tmp_path, monkeypatch)
    con = pi.connect()
    try:
        con.execute("""INSERT INTO my_prices (country, city, date, net_price)
                       VALUES ('LV', 'Riga', '2026-05-01', 1.42)""")
        con.commit()
        assert con.execute(
            "SELECT tenant_id FROM my_prices").fetchone()[0] == "default"
    finally:
        con.close()


def test_benchmark_idempotent_second_connect(tmp_path, monkeypatch):
    pi = _fresh_pi(tmp_path, monkeypatch)
    pi.connect().close()
    monkeypatch.setattr(pi, "_MIGRATED", set())
    con = pi.connect()   # must NOT raise
    try:
        cols = [r["name"] for r in
                con.execute("PRAGMA table_info(my_prices)").fetchall()]
        assert cols.count("tenant_id") == 1
    finally:
        con.close()


# ── portal_scraper.py → portal.db ──────────────────────────────────────────────

PORTAL_TABLES = ["portal_configs", "portal_credentials", "portal_runs"]


def _fresh_ps(tmp_path, monkeypatch):
    import portal_scraper
    importlib.reload(portal_scraper)
    monkeypatch.setattr(portal_scraper, "DB", str(tmp_path / "portal.db"))
    monkeypatch.setattr(portal_scraper, "_SCHEMA_READY", set())
    return portal_scraper


def test_portal_tables_have_tenant_id_default(tmp_path, monkeypatch):
    ps = _fresh_ps(tmp_path, monkeypatch)
    con = ps.connect()
    try:
        for table in PORTAL_TABLES:
            _assert_tenant_col(con, table)
    finally:
        con.close()


def test_portal_default_backfills_unspecified_insert(tmp_path, monkeypatch):
    """A run row inserted without tenant_id backfills 'default'."""
    ps = _fresh_ps(tmp_path, monkeypatch)
    con = ps.connect()
    try:
        con.execute("INSERT INTO portal_runs (supplier, entity, status) "
                    "VALUES ('DEMO', 'E', 'ok')")
        con.commit()
        assert con.execute(
            "SELECT tenant_id FROM portal_runs").fetchone()[0] == "default"
    finally:
        con.close()


def test_portal_config_roundtrip_stamps_default_and_excludes_tenant(tmp_path, monkeypatch):
    """The SELECT*-exposure fix: get_config()/list_configs() return the config
    contract WITHOUT tenant_id, while the stored row IS stamped 'default'."""
    ps = _fresh_ps(tmp_path, monkeypatch)
    ps.set_config("DEMO", "demo", base_url="http://x", config={"a": 1})
    cfg = ps.get_config("DEMO")
    assert "tenant_id" not in cfg, "get_config leaked tenant_id"
    assert cfg["supplier"] == "DEMO" and cfg["enabled"] is True
    listed = [c for c in ps.list_configs() if c["supplier"] == "DEMO"]
    assert listed and "tenant_id" not in listed[0], "list_configs leaked tenant_id"
    # the row itself is stamped with the default tenant.
    con = ps.connect()
    try:
        assert con.execute(
            "SELECT tenant_id FROM portal_configs WHERE supplier='DEMO'"
        ).fetchone()[0] == "default"
    finally:
        con.close()


def test_portal_credentials_roundtrip_unaffected(tmp_path, monkeypatch):
    """Adding tenant_id alongside the envelope-encrypted secret_enc/extra BLOBs must
    not touch the credential crypto: a set/get round-trip still returns the plaintext
    secret, get_credentials() exposes its explicit dict WITHOUT tenant_id, and the
    stored row is stamped 'default'."""
    ps = _fresh_ps(tmp_path, monkeypatch)
    ps.set_credentials("DEMO", "EntA", "user1", "s3cr3t", extra={"acct": "X"})
    cred = ps.get_credentials("DEMO", "EntA")
    assert cred["username"] == "user1"
    assert cred["secret"] == "s3cr3t"          # envelope decrypt intact
    assert cred["extra"] == {"acct": "X"}
    assert "tenant_id" not in cred             # explicit dict, never leaked
    con = ps.connect()
    try:
        assert con.execute(
            "SELECT tenant_id FROM portal_credentials WHERE supplier='DEMO'"
        ).fetchone()[0] == "default"
    finally:
        con.close()


def test_portal_idempotent_second_connect(tmp_path, monkeypatch):
    ps = _fresh_ps(tmp_path, monkeypatch)
    ps.connect().close()
    monkeypatch.setattr(ps, "_SCHEMA_READY", set())
    con = ps.connect()   # must NOT raise
    try:
        cols = [r["name"] for r in
                con.execute("PRAGMA table_info(portal_credentials)").fetchall()]
        assert cols.count("tenant_id") == 1
    finally:
        con.close()
