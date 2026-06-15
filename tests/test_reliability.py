"""Supplier RELIABILITY (slice R1) — the advertised-price historical store and the
overcharge/reliability comparison engine in pricing_intelligence.py.

Three concerns:
  1. HISTORY RETENTION — load_advertised_prices is append-only: loading 2026-05 then
     2026-06 for the SAME supplier/location keeps BOTH dated rows (no period wipe, unlike
     load_my_prices). Re-uploading one (supplier,country,city,date,product) corrects just
     that row via OR REPLACE.
  2. ENGINE CORRECTNESS — reliability_report compares each invoiced fill's effective NET
     price (net_eur_eff/qty) against the advertised price effective on the fill date
     (exact-date, else carry-forward the latest prior quote). A fill invoiced ABOVE
     advertised is an overcharge (score < 1); AT/below is within tolerance (score 1.0);
     a fill with no advertised reference is UNMATCHED (excluded from the score, counted).
  3. TENANT ISOLATION — the advertised store stamps/scopes by the bound tenant behind the
     `multitenant` switch (OFF = byte-identical: stamps 'default', reads unscoped).

The advertised_prices PRIMARY KEY (supplier,country,city,date,product_group) is NOT
tenant-qualified (a deliberate match to the my_prices/portal_configs flagged items —
flagged in code, NOT rekeyed in this slice); the isolation test therefore uses DISTINCT
supplier/location per tenant, exactly as the CRM/portal harnesses use distinct codes.

Prices are NET EUR/L, final (VAT excluded, rebates applied). `transactions` is read
READ-ONLY via the product boundary; benchmark.db is app-owned (the real write path).
"""
import importlib
import sqlite3

import pytest


# ── transactions seed: the engine-owned product DB, with tenant_id ───────────────
# columns the reliability read touches: supplier, country, station(=city), date,
# product_group, qty, net_eur_eff, tenant_id (+ period for completeness).
def _seed_transactions(db, rows):
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE IF NOT EXISTS transactions (
        supplier TEXT, country TEXT, station TEXT, date TEXT, period TEXT,
        product_group TEXT, qty REAL, net_eur_eff REAL,
        tenant_id TEXT NOT NULL DEFAULT 'default')""")
    con.executemany(
        """INSERT INTO transactions
           (supplier, country, station, date, period, product_group, qty,
            net_eur_eff, tenant_id)
           VALUES (?,?,?,?,?,?,?,?,?)""", rows)
    con.commit(); con.close()


@pytest.fixture()
def pi(tmp_path, monkeypatch):
    """Fresh benchmark.db (advertised store, app-owned) + a fuel_history.db
    (transactions, read read-only) pointed at tmp files, multitenant OFF (default)."""
    import dataproduct
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    fh = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(pricing_intelligence, "DB", fh)
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))
    monkeypatch.setattr(pricing_intelligence, "_MIGRATED", set())
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    return pricing_intelligence


# ── 1. HISTORY RETENTION ─────────────────────────────────────────────────────────

def test_advertised_prices_are_kept_historically(pi):
    """Loading May then June for the SAME supplier/location keeps BOTH dated rows —
    append-only, no period wipe."""
    pi.load_advertised_prices([
        {"supplier": "Q8", "country": "LV", "city": "Riga",
         "date": "2026-05-10", "product_group": "Diesel", "net_price": 1.40}])
    pi.load_advertised_prices([
        {"supplier": "Q8", "country": "LV", "city": "Riga",
         "date": "2026-06-10", "product_group": "Diesel", "net_price": 1.45}])
    rows = pi.list_advertised_prices(supplier="Q8")
    dates = sorted(r["date"] for r in rows)
    assert dates == ["2026-05-10", "2026-06-10"]   # May NOT wiped by the June load
    prices = {r["date"]: r["net_price"] for r in rows}
    assert prices["2026-05-10"] == 1.40 and prices["2026-06-10"] == 1.45


def test_reupload_same_key_corrects_only_that_row(pi):
    """Re-uploading the same (supplier,country,city,date,product) OR-REPLACEs just that
    row; other dated rows are retained."""
    pi.load_advertised_prices([
        ("Q8", "LV", "Riga", "2026-05-10", "Diesel", 1.40),
        ("Q8", "LV", "Riga", "2026-06-10", "Diesel", 1.45)])
    # correct only the May row
    pi.load_advertised_prices([("Q8", "LV", "Riga", "2026-05-10", "Diesel", 1.42)])
    rows = {r["date"]: r["net_price"] for r in pi.list_advertised_prices(supplier="Q8")}
    assert rows == {"2026-05-10": 1.42, "2026-06-10": 1.45}   # June untouched


def test_add_advertised_price_single_wrapper_and_normalization(pi):
    """add_advertised_price inserts one row; supplier/country normalise upper-case."""
    pi.add_advertised_price("q8", "lv", "Riga", "2026-05-10", 1.40)
    rows = pi.list_advertised_prices()
    assert len(rows) == 1
    assert rows[0]["supplier"] == "Q8" and rows[0]["country"] == "LV"
    assert rows[0]["city"] == "Riga" and rows[0]["net_price"] == 1.40
    assert rows[0]["source"] == "manual"
    # tenant_id plumbing is hidden from the exposed dict
    assert "tenant_id" not in rows[0]


# ── 2. ENGINE CORRECTNESS ────────────────────────────────────────────────────────

def test_overcharge_and_reliability_score(pi):
    """A supplier invoiced ABOVE advertised -> overcharge_eur>0, overcharged_fills>0,
    reliability_score<1; a supplier invoiced AT/below advertised -> 0 overcharge,
    score 1.0; an unmatched fill is excluded from matched/score and counted unmatched."""
    _seed_transactions(pi.DB, [
        # OVER: Q8 advertised 1.40, invoiced 1.50 (delta +0.10 > tol) on 200 L.
        ("Q8", "LV", "Riga", "2026-05-10", "2026-05", "Diesel", 200.0, 300.0, "default"),
        # AT/BELOW: BP advertised 1.40, invoiced 1.38 (delta -0.02) on 100 L.
        ("BP", "LV", "Riga", "2026-05-10", "2026-05", "Diesel", 100.0, 138.0, "default"),
        # UNMATCHED: SHELL has no advertised reference at all.
        ("SHELL", "LV", "Riga", "2026-05-10", "2026-05", "Diesel", 100.0, 150.0, "default"),
    ])
    pi.load_advertised_prices([
        ("Q8", "LV", "Riga", "2026-05-10", "Diesel", 1.40),
        ("BP", "LV", "Riga", "2026-05-10", "Diesel", 1.40)])

    rep = pi.reliability_report()
    by_sup = {s["supplier"]: s for s in rep["suppliers"]}

    # Q8 overcharged: delta 0.10 * 200 L = 20.00 EUR.
    q8 = by_sup["Q8"]
    assert q8["matched_fills"] == 1
    assert q8["overcharged_fills"] == 1
    assert q8["total_overcharge_eur"] == 20.00
    assert q8["reliability_score"] == 0.0     # 0 of 1 within tolerance
    assert q8["avg_delta_eur_per_l"] == 0.10

    # BP at/below: zero overcharge, perfect score.
    bp = by_sup["BP"]
    assert bp["matched_fills"] == 1
    assert bp["overcharged_fills"] == 0
    assert bp["total_overcharge_eur"] == 0.0
    assert bp["reliability_score"] == 1.0

    # SHELL unmatched: not in matched/score, counted as unmatched.
    shell = by_sup["SHELL"]
    assert shell["matched_fills"] == 0
    assert shell["unmatched_fills"] == 1
    assert shell["reliability_score"] is None

    # detail lists only the overcharged Q8 fill; sorted desc, top is Q8.
    assert len(rep["detail"]) == 1
    assert rep["detail"][0]["supplier"] == "Q8"
    assert rep["detail"][0]["overcharge_eur"] == 20.00

    # summary aggregates across suppliers.
    assert rep["summary"]["matched_fills"] == 2     # Q8 + BP
    assert rep["summary"]["overcharged_fills"] == 1
    assert rep["summary"]["unmatched_fills"] == 1
    assert rep["summary"]["total_overcharge_eur"] == 20.00

    # suppliers sorted by total_overcharge_eur desc -> Q8 first.
    assert rep["suppliers"][0]["supplier"] == "Q8"


def test_matching_is_case_insensitive(pi):
    """REGRESSION: the store upper-cases supplier+country but the fills carry the
    transactions' own casing (country "Poland", station "SUWALKI"). Matching must be
    case/whitespace-insensitive on BOTH sides — otherwise a real "Poland" fill never
    finds its advertised "POLAND" row and silently reports zero overcharges."""
    _seed_transactions(pi.DB, [
        # invoiced 1.50 (300/200); advertised 1.44 -> delta +0.06 > tol -> overcharge.
        ("BP", "Poland", "SUWALKI", "2026-05-31", "2026-05", "Diesel", 200.0, 300.0, "default"),
    ])
    # add_advertised_price upper-cases supplier+country at write ("POLAND"); the fill
    # carries "Poland". A pre-fix engine keyed on raw casing would NOT match this.
    pi.add_advertised_price("BP", "Poland", "SUWALKI", "2026-05-31", 1.44)

    rep = pi.reliability_report()
    by_sup = {s["supplier"]: s for s in rep["suppliers"]}
    assert by_sup["BP"]["matched_fills"] == 1            # matched despite case skew
    assert by_sup["BP"]["overcharged_fills"] == 1
    assert by_sup["BP"]["total_overcharge_eur"] == 12.00  # 0.06 * 200 L
    assert rep["summary"]["unmatched_fills"] == 0


def test_tolerance_not_flagged(pi):
    """A fill invoiced just within the per-litre tolerance is NOT an overcharge."""
    _seed_transactions(pi.DB, [
        # advertised 1.40, invoiced 1.405 -> delta 0.005 <= 0.01 tol.
        ("Q8", "LV", "Riga", "2026-05-10", "2026-05", "Diesel", 100.0, 140.5, "default")])
    pi.load_advertised_prices([("Q8", "LV", "Riga", "2026-05-10", "Diesel", 1.40)])
    rep = pi.reliability_report()
    q8 = rep["suppliers"][0]
    assert q8["matched_fills"] == 1
    assert q8["overcharged_fills"] == 0
    assert q8["reliability_score"] == 1.0
    assert rep["detail"] == []


def test_carry_forward_uses_latest_prior_advertised(pi):
    """A fill dated AFTER the latest advertised row matches the most recent prior quote
    (carry-forward); an exact-date row wins over carry-forward."""
    _seed_transactions(pi.DB, [
        # fill on 2026-05-20, latest advertised row is 2026-05-15 -> carry forward 1.50.
        ("Q8", "LV", "Riga", "2026-05-20", "2026-05", "Diesel", 100.0, 160.0, "default"),
        # fill on 2026-05-15 -> exact-date match 1.50 (not the earlier 1.40).
        ("BP", "LV", "Riga", "2026-05-15", "2026-05", "Diesel", 100.0, 150.0, "default"),
    ])
    pi.load_advertised_prices([
        ("Q8", "LV", "Riga", "2026-05-01", "Diesel", 1.40),
        ("Q8", "LV", "Riga", "2026-05-15", "Diesel", 1.50),
        ("BP", "LV", "Riga", "2026-05-01", "Diesel", 1.40),
        ("BP", "LV", "Riga", "2026-05-15", "Diesel", 1.50)])
    rep = pi.reliability_report()
    by_sup = {s["supplier"]: s for s in rep["suppliers"]}
    # Q8: invoiced 1.60 vs carried-forward 1.50 -> delta 0.10 overcharge.
    assert by_sup["Q8"]["matched_fills"] == 1
    assert by_sup["Q8"]["overcharged_fills"] == 1
    assert by_sup["Q8"]["total_overcharge_eur"] == 10.00   # 0.10 * 100
    # BP: invoiced 1.50 vs exact-date 1.50 -> no overcharge (carry-forward of 1.40 would
    # have spuriously flagged it).
    assert by_sup["BP"]["overcharged_fills"] == 0
    assert by_sup["BP"]["reliability_score"] == 1.0


def test_fill_before_any_advertised_is_unmatched(pi):
    """A fill dated BEFORE the earliest advertised row has no on-or-before reference ->
    unmatched, excluded from the score."""
    _seed_transactions(pi.DB, [
        ("Q8", "LV", "Riga", "2026-04-30", "2026-04", "Diesel", 100.0, 160.0, "default")])
    pi.load_advertised_prices([("Q8", "LV", "Riga", "2026-05-01", "Diesel", 1.40)])
    rep = pi.reliability_report()
    q8 = rep["suppliers"][0]
    assert q8["matched_fills"] == 0
    assert q8["unmatched_fills"] == 1
    assert q8["reliability_score"] is None


def test_period_filter_scopes_the_fill_month(pi):
    """period='YYYY-MM' filters on the fill month; None = all history."""
    _seed_transactions(pi.DB, [
        ("Q8", "LV", "Riga", "2026-05-10", "2026-05", "Diesel", 100.0, 150.0, "default"),
        ("Q8", "LV", "Riga", "2026-06-10", "2026-06", "Diesel", 100.0, 150.0, "default"),
    ])
    pi.load_advertised_prices([
        ("Q8", "LV", "Riga", "2026-05-01", "Diesel", 1.40),
        ("Q8", "LV", "Riga", "2026-06-01", "Diesel", 1.40)])
    may = pi.reliability_report(period="2026-05")
    assert may["summary"]["matched_fills"] == 1
    allp = pi.reliability_report()
    assert allp["summary"]["matched_fills"] == 2


# ── 3. TENANT ISOLATION (behind the switch) ──────────────────────────────────────

@pytest.fixture()
def pit(tmp_path, monkeypatch):
    """Fresh benchmark.db + tenant-stamped fuel_history.db + security.db (registry),
    multitenant ON. Yields (pricing_intelligence, tenancy)."""
    import auth
    import dataproduct
    import tenancy
    import pricing_intelligence
    importlib.reload(pricing_intelligence)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    fh = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))
    monkeypatch.setattr(pricing_intelligence, "DB", fh)
    monkeypatch.setattr(pricing_intelligence, "_MIGRATED", set())
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)

    # DISTINCT supplier/location per tenant sidesteps the (un-tenant-qualified)
    # advertised_prices PK — same family as the my_prices/portal_configs flagged items.
    _seed_transactions(fh, [
        ("SUPA", "LV", "Riga", "2026-05-10", "2026-05", "Diesel", 100.0, 150.0, "A"),
        ("SUPB", "LT", "Vilnius", "2026-05-10", "2026-05", "Diesel", 100.0, 150.0, "B"),
    ])

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    try:
        yield pricing_intelligence, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_two_tenants(pi, tenancy):
    """As tenant A advertise SUPA/Riga; as tenant B advertise SUPB/Vilnius."""
    tenancy.set_tenant("A")
    pi.add_advertised_price("SUPA", "LV", "Riga", "2026-05-01", 1.40)
    tenancy.set_tenant("B")
    pi.add_advertised_price("SUPB", "LT", "Vilnius", "2026-05-01", 1.40)
    tenancy.reset_tenant()


def test_advertised_write_stamps_bound_tenant(pit):
    pi, tenancy = pit
    _seed_two_tenants(pi, tenancy)
    tenancy.set_owner_scope()
    con = pi.connect()
    try:
        rows = {r["supplier"]: r["tenant_id"] for r in
                con.execute("SELECT supplier, tenant_id FROM advertised_prices")}
    finally:
        con.close()
    assert rows == {"SUPA": "A", "SUPB": "B"}


def test_tenant_reads_only_own_advertised(pit):
    pi, tenancy = pit
    _seed_two_tenants(pi, tenancy)
    tenancy.set_tenant("A")
    assert {r["supplier"] for r in pi.list_advertised_prices()} == {"SUPA"}
    tenancy.set_tenant("B")
    assert {r["supplier"] for r in pi.list_advertised_prices()} == {"SUPB"}


def test_owner_scope_sees_both_advertised(pit):
    pi, tenancy = pit
    _seed_two_tenants(pi, tenancy)
    tenancy.set_owner_scope()
    assert {r["supplier"] for r in pi.list_advertised_prices()} == {"SUPA", "SUPB"}


def test_reliability_is_intra_tenant(pit):
    """As tenant A, reliability matches A's SUPA fill against A's advertised price only;
    B's SUPB advertised price (and fill) never enter A's report."""
    pi, tenancy = pit
    _seed_two_tenants(pi, tenancy)
    tenancy.set_tenant("A")
    rep_a = pi.reliability_report()
    tenancy.reset_tenant()
    assert {s["supplier"] for s in rep_a["suppliers"]} == {"SUPA"}
    # SUPA invoiced 1.50 vs advertised 1.40 -> matched, overcharged.
    a = rep_a["suppliers"][0]
    assert a["supplier"] == "SUPA" and a["matched_fills"] == 1
    assert a["overcharged_fills"] == 1

    tenancy.set_tenant("B")
    rep_b = pi.reliability_report()
    tenancy.reset_tenant()
    assert {s["supplier"] for s in rep_b["suppliers"]} == {"SUPB"}


def test_advertised_write_without_tenant_or_owner_raises(pit):
    pi, tenancy = pit
    tenancy.reset_tenant()      # switch ON, neither tenant nor owner bound
    with pytest.raises(RuntimeError):
        pi.add_advertised_price("NOPE", "LV", "Riga", "2026-05-01", 1.0)


def test_advertised_write_under_owner_scope_raises(pit):
    pi, tenancy = pit
    tenancy.set_owner_scope()   # owner scope is READ-ONLY
    with pytest.raises(RuntimeError):
        pi.add_advertised_price("NOPE", "LV", "Riga", "2026-05-01", 1.0)


# ── OFF regression: byte-identical to today ─────────────────────────────────────

def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    """OFF (default): writes stamp 'default' (== the column DEFAULT) and reads are
    unscoped — identical to today, even with a thread tenant bound."""
    import auth
    import dataproduct
    import tenancy
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    fh = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))
    monkeypatch.setattr(pricing_intelligence, "DB", fh)
    monkeypatch.setattr(pricing_intelligence, "_MIGRATED", set())
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    _seed_transactions(fh, [
        ("Q8", "LV", "Riga", "2026-05-10", "2026-05", "Diesel", 100.0, 150.0, "A")])
    assert tenancy.multitenant_enabled() is False

    # Even with a tenant set, OFF keeps write_tenant/scope_clause inert.
    tenancy.set_tenant("A")
    pricing_intelligence.add_advertised_price("Q8", "LV", "Riga", "2026-05-01", 1.40)
    tenancy.reset_tenant()

    con = pricing_intelligence.connect()
    try:
        row = con.execute(
            "SELECT tenant_id FROM advertised_prices WHERE supplier='Q8'").fetchone()
        assert row["tenant_id"] == "default"   # stamped the column DEFAULT
    finally:
        con.close()

    # Reads/reliability are unscoped regardless of any thread tenant.
    tenancy.set_tenant("ZZZ")
    try:
        assert {r["supplier"] for r in pricing_intelligence.list_advertised_prices()} == {"Q8"}
        rep = pricing_intelligence.reliability_report()
        assert {s["supplier"] for s in rep["suppliers"]} == {"Q8"}
    finally:
        tenancy.reset_tenant()
