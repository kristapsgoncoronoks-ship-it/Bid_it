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


def _seed_multi_quarter(tmp_path, monkeypatch):
    """ACME (Germany) with txns in two quarters; BETA (Poland) in one. Returns claims con."""
    import vat_refund
    importlib.reload(vat_refund)
    analytics = tmp_path / "fuel_history.db"
    a = sqlite3.connect(str(analytics))
    a.execute("""CREATE TABLE transactions (entity, country, currency, period,
                 vat_eur, vat_local)""")
    for ent, ctry, ccy, per in [("ACME", "Germany", "EUR", "2026-01"),
                                ("ACME", "Germany", "EUR", "2026-04"),
                                ("BETA", "Poland", "PLN", "2026-02")]:
        a.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?)",
                  (ent, ctry, ccy, per, 500, 600))
    a.commit(); a.close()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(analytics))
    vat_refund._SCHEMA_READY.clear()
    return vat_refund


def test_claim_matrix_with_portal_false_skips_portal(tmp_path, monkeypatch):
    """with_portal=False yields falsy home and never calls customer_master.portal."""
    vat_refund = _seed_multi_quarter(tmp_path, monkeypatch)
    import customer_master
    calls = {"n": 0}
    real = customer_master.portal
    monkeypatch.setattr(customer_master, "portal",
                        lambda ent: (calls.__setitem__("n", calls["n"] + 1) or real(ent)))
    rows = vat_refund.claim_matrix(vat_refund.connect(), "2026", with_portal=False)
    assert rows, "expected matrix rows"
    assert all(not r["home"] for r in rows)
    assert calls["n"] == 0


def test_claim_matrix_default_memoises_portal_per_entity(tmp_path, monkeypatch):
    """Default (with_portal=True) populates home and calls portal AT MOST once per entity,
    not once per quarter/YEAR row."""
    vat_refund = _seed_multi_quarter(tmp_path, monkeypatch)
    import customer_master
    seen = []
    real = customer_master.portal
    monkeypatch.setattr(customer_master, "portal",
                        lambda ent: (seen.append(ent) or real(ent)))
    rows = vat_refund.claim_matrix(vat_refund.connect(), "2026")
    entities = {r["entity"] for r in rows}
    # ACME has 2 quarter rows + 1 YEAR row = 3 rows but must be looked up at most once
    acme_rows = [r for r in rows if r["entity"] == "ACME"]
    assert len(acme_rows) >= 3
    assert len(seen) <= len(entities)                 # <= distinct entities, never per-row
    assert seen.count("ACME") <= 1
    assert all("home" in r for r in rows)             # home key populated on default path


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
