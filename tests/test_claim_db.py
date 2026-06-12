"""The VAT-refund claim records live in their OWN database, isolated from the
analytics/transactions store, and migrate across from the old shared DB on upgrade."""
import importlib
import sqlite3


def test_claim_db_separate_and_migrates(tmp_path, monkeypatch):
    import vat_refund
    importlib.reload(vat_refund)
    claims = tmp_path / "vat_claims.db"
    analytics = tmp_path / "fuel_history.db"

    # the old shared DB: transactions AND (legacy) claim tables
    a = sqlite3.connect(str(analytics))
    a.execute("""CREATE TABLE transactions (entity, country, currency, period,
                 vat_eur, vat_local)""")
    a.execute("INSERT INTO transactions VALUES ('ACME','Germany','EUR','2026-05',100,120)")
    a.execute("CREATE TABLE vat_applications (entity, refund_country, ref_period, status)")
    a.execute("INSERT INTO vat_applications VALUES ('ACME','Germany','2026-Q2','submitted')")
    a.commit(); a.close()

    monkeypatch.setattr(vat_refund, "DB", str(claims))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(analytics))
    vat_refund._SCHEMA_READY.clear()

    con = vat_refund.connect()
    # the claim row was migrated into the SEPARATE claims DB
    assert con.execute("SELECT status FROM vat_applications WHERE entity='ACME'"
                       ).fetchone()[0] == "submitted"
    # transactions are NOT in the claims DB (they stay in analytics)
    assert con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'"
                       ).fetchone() is None
    con.close()
    assert claims.exists() and analytics.exists()

    # claim_matrix reads transactions from the analytics DB while claims live elsewhere
    m = vat_refund.claim_matrix(vat_refund.connect(), "2026")
    assert any(r["entity"] == "ACME" and r["country"] == "Germany" for r in m)


def test_migration_is_idempotent(tmp_path, monkeypatch):
    import vat_refund
    importlib.reload(vat_refund)
    analytics = tmp_path / "fuel_history.db"
    a = sqlite3.connect(str(analytics))
    a.execute("CREATE TABLE vat_applications (entity, refund_country, ref_period, status)")
    a.execute("INSERT INTO vat_applications VALUES ('ACME','Germany','2026-Q2','paid')")
    a.commit(); a.close()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(analytics))
    vat_refund._SCHEMA_READY.clear()
    vat_refund.connect().close()
    # a second process (cache cleared) must NOT re-import duplicates
    vat_refund._SCHEMA_READY.clear()
    con = vat_refund.connect()
    assert con.execute("SELECT COUNT(*) FROM vat_applications").fetchone()[0] == 1
    con.close()


def test_goods_codes_match_directive_art9():
    """Art. 9 codes (2008/9/EC): road tolls = 4 (not generic 3); AdBlue/parking/other
    residual = 10 (NOT 9 = luxuries/entertainment, which is non-deductible)."""
    import vat_config
    importlib.reload(vat_config)
    gc = vat_config.GOODS_CODE
    assert gc["Toll/Fees"][0] == "4"
    assert gc["AdBlue"][0] == "10"
    assert gc["Parking"][0] == "10"
    assert gc["Service/Other"][0] == "10"
    assert all(code != "9" for code, _desc in gc.values())  # code 9 = luxuries, never used
    assert gc["Diesel"][0] == "1"                            # fuel mappings unchanged
