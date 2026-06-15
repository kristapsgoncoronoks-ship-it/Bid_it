"""Multi-tenancy P2 — CROSS-TENANT ISOLATION harness for pricing_intelligence.py.

The ANTITRUST-critical slice (docs/SECURITY_COMPLIANCE_PLAN.md §7): the benchmark
MUST stay intra-tenant. A client may only ever benchmark its OWN entities; the peer
cohort must NEVER pool across clients. This file mirrors the proven CRM template
(test_tenant_isolation_crm.py) on the price-intelligence module, behind the
`multitenant` switch:

  * WRITES stamp the bound tenant (`tenancy.write_tenant()`) — a `load_my_prices`
    row created "as tenant A" carries tenant_id='A'; the replace-period DELETE only
    touches the bound tenant's own rows.
  * READS filter by `tenancy.scope_clause()`, so as tenant A `_my_price_lookup` /
    `margin_report` / `internal_benchmark` / `peer_benchmark` see ONLY A's prices and
    A's entities — B's data is absent from A's benchmark (the antitrust no-bleed
    proof). The platform OWNER sees BOTH (the audited cross-tenant analytics
    exception). A tenant-less write FAILS LOUD.

CARDINAL invariant: with the switch OFF (default) writes stamp 'default' and reads
are unscoped — byte-identical to today. The existing pricing/benchmark suite
(test_pricing.py / test_pricing_intel*.py) is the standing OFF proof; this file adds
one explicit OFF assertion alongside the ON isolation proofs.

The benchmark tables (my_prices/wholesale_prices) live in benchmark.db (app-owned,
written via the real load_my_prices path). `transactions` lives in the engine-owned
fuel_history.db, read READ-ONLY via the product boundary — seeded here with directly
tenant-stamped rows (as the B2 schema fixture establishes the column).
"""
import importlib
import sqlite3

import pytest


# ── transactions seed: the engine-owned product DB, with tenant_id (B2) ──────────
# columns the pricing reads touch: entity, supplier, country, station(=city), date,
# period, product_group, qty, net_eur_eff, tenant_id.
def _seed_transactions(db, rows):
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE IF NOT EXISTS transactions (
        entity TEXT, supplier TEXT, country TEXT, station TEXT, date TEXT,
        period TEXT, product_group TEXT, qty REAL, net_eur_eff REAL,
        tenant_id TEXT NOT NULL DEFAULT 'default')""")
    con.executemany(
        """INSERT INTO transactions
           (entity, supplier, country, station, date, period, product_group,
            qty, net_eur_eff, tenant_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
    con.commit(); con.close()


PERIOD = "2099-09"


@pytest.fixture()
def pi(tmp_path, monkeypatch):
    """A fresh benchmark.db + a tenant-stamped fuel_history.db (transactions) +
    security.db, with the `multitenant` switch ON. Yields (pricing_intelligence,
    tenancy)."""
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

    # Two entities, same country/city/period, different effective €/L, under tenant A;
    # a separate set under tenant B. The transactions read paths (supplier_grid,
    # internal_benchmark, peer_benchmark) must only ever see the bound tenant's rows.
    _seed_transactions(fh, [
        # tenant A — entities ENT_A1 (cheap) and ENT_A2 (dear), same LV/Riga cell.
        ("ENT_A1", "TFC", "LV", "Riga", f"{PERIOD}-01", PERIOD, "Diesel", 1000.0, 1500.0, "A"),
        ("ENT_A2", "Q8", "LV", "Riga", f"{PERIOD}-01", PERIOD, "Diesel", 1000.0, 1600.0, "A"),
        # tenant B — a DIFFERENT, much cheaper entity in the SAME cell. If isolation
        # leaks, B's price would pollute A's peer median / internal best-of.
        ("ENT_B1", "BP", "LV", "Riga", f"{PERIOD}-01", PERIOD, "Diesel", 1000.0, 800.0, "B"),
        ("ENT_B2", "BP", "LV", "Riga", f"{PERIOD}-01", PERIOD, "Diesel", 1000.0, 900.0, "B"),
    ])

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    try:
        yield pricing_intelligence, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_my_prices(pi, tenancy):
    """As tenant A load a Riga benchmark of 1.40; as tenant B load 0.50 — both via the
    REAL load_my_prices write path, proving the INSERT stamps the bound tenant and the
    reads only ever match the bound tenant's price.

    NB the my_prices PRIMARY KEY is the natural (country, city, date, product_group) —
    it does NOT include tenant_id (a deliberate match to the CRM P2 slice, where the
    `customers` PK is the shared `code`: the proven pattern scopes reads/writes but does
    not re-key the natural PK). So two tenants cannot hold the SAME natural-key row (an
    INSERT OR REPLACE would collide); the isolation proof therefore uses DISTINCT cells
    per tenant, exactly as the CRM harness uses distinct codes (ACME vs BETA). A's
    benchmark city is Riga, B's is Vilnius — and the SCOPE filter is what keeps each
    tenant's read to its own row even though both sit in the one my_prices table."""
    tenancy.set_tenant("A")
    pi.load_my_prices([{"country": "LV", "city": "Riga",
                        "date": f"{PERIOD}-01", "net_price": 1.40}])
    tenancy.set_tenant("B")
    pi.load_my_prices([{"country": "LV", "city": "Vilnius",
                        "date": f"{PERIOD}-01", "net_price": 0.50}])
    tenancy.reset_tenant()


# ── WRITE stamping ──────────────────────────────────────────────────────────────

def test_load_my_prices_stamps_the_bound_tenant(pi):
    pi_mod, tenancy = pi
    _seed_my_prices(pi_mod, tenancy)
    tenancy.set_owner_scope()
    con = pi_mod.connect()
    try:
        rows = sorted((r["tenant_id"], r["city"], r["net_price"])
                      for r in con.execute(
                          "SELECT tenant_id, city, net_price FROM my_prices"))
    finally:
        con.close()
    assert rows == [("A", "Riga", 1.40), ("B", "Vilnius", 0.50)]


def test_replace_period_only_clears_own_tenant(pi):
    """load_my_prices(replace_period=...) as tenant A must NOT delete tenant B's row
    for the same period — the DELETE is tenant-scoped."""
    pi_mod, tenancy = pi
    _seed_my_prices(pi_mod, tenancy)
    tenancy.set_tenant("A")
    pi_mod.load_my_prices(
        [{"country": "LV", "city": "Riga", "date": f"{PERIOD}-15", "net_price": 1.45}],
        replace_period=PERIOD)
    tenancy.reset_tenant()
    tenancy.set_owner_scope()
    con = pi_mod.connect()
    try:
        by_tenant = {}
        for r in con.execute("SELECT tenant_id, net_price FROM my_prices"):
            by_tenant.setdefault(r["tenant_id"], []).append(r["net_price"])
    finally:
        con.close()
    # A replaced its OWN period rows (the original 1.40 gone, now 1.45); B's 0.50
    # survived A's replace-period — the DELETE never reached another tenant's rows.
    assert by_tenant["A"] == [1.45]
    assert by_tenant["B"] == [0.50]


# ── READ isolation (the antitrust no-bleed proof) ───────────────────────────────

def test_my_price_lookup_is_tenant_scoped(pi):
    pi_mod, tenancy = pi
    _seed_my_prices(pi_mod, tenancy)
    # As tenant A, the Riga benchmark resolves to A's OWN 1.40.
    tenancy.set_tenant("A")
    con = pi_mod.connect()
    try:
        price, match = pi_mod._my_price_lookup(con, "LV", "Riga", f"{PERIOD}-01")
    finally:
        con.close()
    assert price == 1.40 and match == "exact"
    # As tenant B, Riga is A's city — B holds NO Riga price (only Vilnius). B does NOT
    # see A's exact 1.40 Riga benchmark; the cascade falls through to B's OWN LV
    # country-avg (Vilnius 0.50). The no-bleed proof: B never inherits A's 1.40.
    tenancy.set_tenant("B")
    con = pi_mod.connect()
    try:
        price, match = pi_mod._my_price_lookup(con, "LV", "Riga", f"{PERIOD}-01")
    finally:
        con.close()
    assert price == 0.50 and match == "country-avg"   # B's own LV avg, never A's 1.40
    # B's OWN Vilnius price is visible to B.
    tenancy.set_tenant("B")
    con = pi_mod.connect()
    try:
        price, match = pi_mod._my_price_lookup(con, "LV", "Vilnius", f"{PERIOD}-01")
    finally:
        con.close()
    assert price == 0.50 and match == "exact"


def test_margin_report_uses_only_own_prices(pi):
    pi_mod, tenancy = pi
    _seed_my_prices(pi_mod, tenancy)
    tenancy.set_tenant("A")
    rows, _ = pi_mod.margin_report(PERIOD)
    # Every matched row used A's 1.40 benchmark — never B's 0.50.
    matched = [r for r in rows if r["my_price"] is not None]
    assert matched
    assert all(r["my_price"] == 1.40 for r in matched), [r["my_price"] for r in matched]
    # Only A's two suppliers appear (B's BP fill is invisible to A's grid).
    assert {r["supplier"] for r in rows} == {"TFC", "Q8"}


def test_internal_benchmark_best_is_own_tenant_only(pi):
    """A's self-sourced best-of must be A's cheaper entity (1.50/L), NOT B's 0.80/L.
    If isolation leaked, B's far cheaper fill would become the 'best' and inflate
    overpay."""
    pi_mod, tenancy = pi
    tenancy.set_tenant("A")
    rows, _ = pi_mod.internal_benchmark(PERIOD)
    tenancy.reset_tenant()
    cell = [r for r in rows if r["country"] == "LV"][0]
    # A's prices: ENT_A1 1500/1000=1.50, ENT_A2 1600/1000=1.60 (per supplier TFC/Q8).
    assert cell["best_price"] == 1.50
    assert cell["best_supplier"] == "TFC"


def test_peer_benchmark_cohort_is_intra_tenant(pi):
    """THE ANTITRUST PROOF. As tenant A the peer cohort is A's own entities only —
    ENT_A1 / ENT_A2 — and B's ENT_B1 / ENT_B2 NEVER enter A's cohort. A client can
    never see, or be benchmarked against, another client's prices."""
    pi_mod, tenancy = pi
    tenancy.set_tenant("A")
    rows, summ = pi_mod.peer_benchmark(PERIOD)
    tenancy.reset_tenant()
    entities = {r["entity"] for r in rows}
    assert entities == {"ENT_A1", "ENT_A2"}
    assert "ENT_B1" not in entities and "ENT_B2" not in entities
    # With exactly 2 entities, each has 1 peer < PEER_MIN_CONTRIBUTORS (2) -> suppressed.
    # The point of this test is the COHORT membership, not the figure: B is absent.
    assert summ["cells"] == 1

    # As tenant B, symmetric: only B's entities.
    tenancy.set_tenant("B")
    rows_b, _ = pi_mod.peer_benchmark(PERIOD)
    tenancy.reset_tenant()
    assert {r["entity"] for r in rows_b} == {"ENT_B1", "ENT_B2"}


# ── OWNER cross-tenant scope (the audited analytics exception) ───────────────────

def test_owner_scope_sees_both_tenants(pi):
    pi_mod, tenancy = pi
    _seed_my_prices(pi_mod, tenancy)
    tenancy.set_owner_scope()
    # my_prices: owner sees both stamped rows.
    con = pi_mod.connect()
    try:
        frag, params = tenancy.scope_clause()
        prices = sorted(r["net_price"] for r in con.execute(
            "SELECT net_price FROM my_prices WHERE 1=1" + frag, params))
    finally:
        con.close()
    assert prices == [0.50, 1.40]
    # peer_benchmark: owner's cohort spans every tenant's entities (de-identified
    # aggregate analytics — the deliberate operator exception).
    rows, _ = pi_mod.peer_benchmark(PERIOD)
    tenancy.reset_tenant()
    assert {r["entity"] for r in rows} == {"ENT_A1", "ENT_A2", "ENT_B1", "ENT_B2"}


# ── WRITE GUARD: a tenant-less write must FAIL LOUD ─────────────────────────────

def test_write_without_tenant_or_owner_raises(pi):
    pi_mod, tenancy = pi
    tenancy.reset_tenant()
    with pytest.raises(RuntimeError):
        pi_mod.load_my_prices([{"country": "LV", "city": "Riga",
                                "date": f"{PERIOD}-01", "net_price": 1.0}])


def test_write_under_owner_scope_raises(pi):
    pi_mod, tenancy = pi
    tenancy.set_owner_scope()   # owner scope is READ-ONLY
    with pytest.raises(RuntimeError):
        pi_mod.load_wholesale([{"country": "LV", "date": f"{PERIOD}-01", "net_price": 1.0}])


# ── OFF regression: byte-identical to today ─────────────────────────────────────

def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    """With the switch OFF (default), writes stamp 'default' (== the column DEFAULT)
    and reads are unscoped — identical to today. Even a thread tenant is inert."""
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
    _seed_transactions(fh, [
        ("ENT_A1", "TFC", "LV", "Riga", f"{PERIOD}-01", PERIOD, "Diesel", 1000.0, 1500.0, "A"),
        ("ENT_B1", "BP", "LV", "Riga", f"{PERIOD}-01", PERIOD, "Diesel", 1000.0, 800.0, "B"),
    ])
    assert tenancy.multitenant_enabled() is False

    # Even with a tenant set, OFF keeps write_tenant/scope_clause inert.
    tenancy.set_tenant("A")
    pricing_intelligence.load_my_prices([{"country": "LV", "city": "Riga",
                                          "date": f"{PERIOD}-01", "net_price": 1.40}])
    tenancy.reset_tenant()

    con = pricing_intelligence.connect()
    try:
        row = con.execute(
            "SELECT tenant_id FROM my_prices WHERE country='LV'").fetchone()
        assert row["tenant_id"] == "default"   # stamped the column DEFAULT
    finally:
        con.close()

    # Reads are unscoped regardless of any thread tenant: a 'ZZZ'-bound thread still
    # sees ALL transactions (both A's and B's entities) — exactly as today.
    tenancy.set_tenant("ZZZ")
    rows, _ = pricing_intelligence.peer_benchmark(PERIOD)
    tenancy.reset_tenant()
    assert {r["entity"] for r in rows} == {"ENT_A1", "ENT_B1"}
