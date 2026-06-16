"""
PRICING INTELLIGENCE - competitor NET-price tracking and margin-policy analysis.

Data model (all from the daily transaction grain - never store rollups):
  transactions          your fills: country, station(=city), date, supplier, qty, net_eur_eff
  my_prices             YOUR benchmark: country, city, date, net_price (NET, final)
  wholesale_prices      external market index: country, date, net_price (optional)

Everything aggregates UP from daily. Time grain (day/week/month) is computed on the
fly by bucketing the date - so any grain is available retroactively from one table.

Margin baselines (all three):
  vs MY Price       supplier effective NET - your benchmark (relative position)
  vs pack           supplier vs the average of the other suppliers same city/period
  vs wholesale      supplier effective NET - wholesale index = TRUE margin (if loaded)

A supplier's effective NET = net_eur_eff / qty (rebates already in net_eur_eff,
VAT excluded). City = the station town on the invoice.
"""
import os, sqlite3, datetime
import money
import tenancy

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# transactions live in the engine-owned product DB (read-only from the app side,
# via dataproduct.connect("fuel_history")). DB names that file for the location-
# independent / test-monkeypatch seam — it is NEVER opened read-write here (D1/D3).
DB = f"{WORKDIR}/fuel_history.db"
# The benchmark tables (my_prices/wholesale_prices) are app/portal-owned and live in
# their OWN read-write DB, decoupled from the product DB in D3.
BENCHMARK_DB = f"{WORKDIR}/benchmark.db"

# Per-entity-vs-peer benchmark (M1) min-cohort gate: a (country, bucket) cell needs at
# least this many OTHER entities (excluding the entity itself) for a peer figure to be
# emitted; fewer and the cell is SUPPRESSED ("cohort too small") so no single entity is
# singled out — sound technical anonymisation that keeps a future cross-client sell open.
PEER_MIN_CONTRIBUTORS = 2

# DDL for the app-owned benchmark tables — applied once per DB via db_migrate
# (APPEND-ONLY; positions are stable).
_BENCHMARK_DDL = [
    """CREATE TABLE IF NOT EXISTS my_prices (
        country TEXT, city TEXT, date TEXT, product_group TEXT DEFAULT 'Diesel',
        net_price REAL, source TEXT DEFAULT 'upload',
        PRIMARY KEY (country, city, date, product_group))""",
    """CREATE TABLE IF NOT EXISTS wholesale_prices (
        country TEXT, date TEXT, product_group TEXT DEFAULT 'Diesel',
        net_price REAL, source TEXT,
        PRIMARY KEY (country, date, product_group))""",
    "CREATE INDEX IF NOT EXISTS ix_myp ON my_prices(country, city, date)",
    "CREATE INDEX IF NOT EXISTS ix_whp ON wholesale_prices(country, date)",
    # ADVERTISED prices — the HISTORICAL store of what a supplier advertised on its
    # portal per (supplier, country, city, date, product). Kept FOREVER (append-only,
    # one row per dated quote): a past invoice can always be checked against the price
    # that applied on its fill date (carry-forward in reliability_report). NET EUR/L,
    # final (VAT excluded). city = the location/station dimension (== transactions.station).
    # PK NOTE: the natural PK below is SUPERSEDED by the tenant-qualified PK rebuilt in
    # the PK-rekey migrations appended at the END of this list (see below) — it is now
    # (tenant_id, supplier, country, city, date, product_group).
    """CREATE TABLE IF NOT EXISTS advertised_prices (
        supplier TEXT, country TEXT, city TEXT, date TEXT,
        product_group TEXT DEFAULT 'Diesel', net_price REAL,
        source TEXT DEFAULT 'upload',
        PRIMARY KEY (supplier, country, city, date, product_group))""",
    "CREATE INDEX IF NOT EXISTS ix_advp ON advertised_prices(supplier, country, city, date)",
] + tenancy.tenant_column_ddls([
    # P1 multi-tenancy (schema plumbing only): stamp the app-owned benchmark price
    # tables with a tenant_id; existing rows backfill to DEFAULT_TENANT_ID via the
    # column DEFAULT, new rows default too. NO query reads this column yet (the
    # `multitenant` switch is OFF and scope_clause is unwired until P2). This is
    # also exactly what P2 will use to keep the antitrust-sensitive benchmark
    # intra-tenant (docs/STRATEGY.md#security-compliance-evolution-plan-operating-as-a-multi-client-saas §7). APPEND-ONLY — keep at END.
    "my_prices", "wholesale_prices", "advertised_prices",
]) + [
    # ── PK RE-KEY (multi-tenant): tenant-qualified PRIMARY KEYs ──────────────────
    # The benchmark tables already carry a tenant_id column (P1, the tenant_column_ddls
    # spread above) and tenant-scoped reads/stamped writes (P2). But their PRIMARY KEYs
    # did NOT include tenant_id, so the INSERT OR REPLACE in the load functions resolves
    # conflicts on the NATURAL key only — under the `multitenant` switch ON two tenants
    # writing the SAME logical key would REPLACE each other (cross-tenant data loss).
    #
    # SQLite cannot ALTER a PRIMARY KEY in place, so each table is REBUILT: create a
    # __rekey twin with tenant_id FIRST in the PK (every other column, type, DEFAULT and
    # the rest of the key order preserved), copy all rows (explicit column list — never
    # rely on column order), drop the old, rename the twin, then recreate the indexes the
    # DROP destroyed. This runs as a LATER one-time db_migrate migration (versioned, runs
    # exactly once per DB) that supersedes the PK on both fresh and existing benchmark.db
    # files. Benchmark tables are NOT audited (pricing_intelligence.connect installs no
    # audit triggers), so there are no triggers to drop/reinstate. APPEND-ONLY — these
    # must stay at the END (positions are stable); do NOT reorder the entries above.
    # OFF-by-default is byte-identical: with a single 'default' tenant the qualified PK
    # behaves exactly as the natural PK did.

    # my_prices: PK (country, city, date, product_group) -> (tenant_id, country, city,
    # date, product_group).
    """CREATE TABLE IF NOT EXISTS my_prices__rekey (
        country TEXT, city TEXT, date TEXT, product_group TEXT DEFAULT 'Diesel',
        net_price REAL, source TEXT DEFAULT 'upload',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (tenant_id, country, city, date, product_group))""",
    """INSERT INTO my_prices__rekey
        (country, city, date, product_group, net_price, source, tenant_id)
        SELECT country, city, date, product_group, net_price, source, tenant_id
        FROM my_prices""",
    "DROP TABLE my_prices",
    "ALTER TABLE my_prices__rekey RENAME TO my_prices",
    "CREATE INDEX IF NOT EXISTS ix_myp ON my_prices(country, city, date)",

    # wholesale_prices: PK (country, date, product_group) -> (tenant_id, country, date,
    # product_group).
    """CREATE TABLE IF NOT EXISTS wholesale_prices__rekey (
        country TEXT, date TEXT, product_group TEXT DEFAULT 'Diesel',
        net_price REAL, source TEXT,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (tenant_id, country, date, product_group))""",
    """INSERT INTO wholesale_prices__rekey
        (country, date, product_group, net_price, source, tenant_id)
        SELECT country, date, product_group, net_price, source, tenant_id
        FROM wholesale_prices""",
    "DROP TABLE wholesale_prices",
    "ALTER TABLE wholesale_prices__rekey RENAME TO wholesale_prices",
    "CREATE INDEX IF NOT EXISTS ix_whp ON wholesale_prices(country, date)",

    # advertised_prices: PK (supplier, country, city, date, product_group) ->
    # (tenant_id, supplier, country, city, date, product_group).
    """CREATE TABLE IF NOT EXISTS advertised_prices__rekey (
        supplier TEXT, country TEXT, city TEXT, date TEXT,
        product_group TEXT DEFAULT 'Diesel', net_price REAL,
        source TEXT DEFAULT 'upload',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (tenant_id, supplier, country, city, date, product_group))""",
    """INSERT INTO advertised_prices__rekey
        (supplier, country, city, date, product_group, net_price, source, tenant_id)
        SELECT supplier, country, city, date, product_group, net_price, source, tenant_id
        FROM advertised_prices""",
    "DROP TABLE advertised_prices",
    "ALTER TABLE advertised_prices__rekey RENAME TO advertised_prices",
    "CREATE INDEX IF NOT EXISTS ix_advp ON advertised_prices(supplier, country, city, date)",
]

# Reliability: per-litre overcharge tolerance — a fill whose invoiced effective NET
# price exceeds the advertised price by no more than this (EUR/L) is treated as
# within tolerance (trivial rounding noise), NOT an overcharge.
OVERCHARGE_TOL_EUR_PER_L = 0.01


def connect():
    """Read-write handle to the app/portal-owned benchmark DB (my_prices/
    wholesale_prices). The engine-owned product DB (fuel_history.db) is NOT opened
    here — readers that need `transactions` use product_connect() (read-only)."""
    import db_tuning, db_migrate
    con = sqlite3.connect(BENCHMARK_DB); con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    db_migrate.apply(con, "pricing_intelligence", _BENCHMARK_DDL)
    _migrate_from_product(con)
    return con


def product_connect():
    """READ-ONLY handle to the engine-owned product DB for reading `transactions`.
    Delegates to the dataproduct accessor so the app shares one read-only window;
    passes this module's DB so the location-independent / test-monkeypatch seam
    (DB) is preserved. The app never writes the product DB."""
    import dataproduct
    return dataproduct.connect("fuel_history", path=DB)


# one-time data copy guard: (resolved BENCHMARK_DB path) once migration has run
_MIGRATED = set()


def _migrate_from_product(con):
    """One-time upgrade path: if the benchmark tables are empty in the (new)
    benchmark.db but populated in the old shared fuel_history.db, copy them across.
    Idempotent — skips once benchmark.db has data (or once per process). The
    originals are LEFT in place in fuel_history.db (same precedent as
    vat_refund._migrate_from_analytics); the engine-owned DB just stops being
    written by the app."""
    if BENCHMARK_DB == ":memory:" or BENCHMARK_DB in _MIGRATED:
        return
    try:
        if con.execute("SELECT COUNT(*) FROM my_prices").fetchone()[0] > 0:
            _MIGRATED.add(BENCHMARK_DB); return
        if con.execute("SELECT COUNT(*) FROM wholesale_prices").fetchone()[0] > 0:
            _MIGRATED.add(BENCHMARK_DB); return
    except sqlite3.Error:
        return
    if not os.path.exists(DB) or os.path.abspath(DB) == os.path.abspath(BENCHMARK_DB):
        _MIGRATED.add(BENCHMARK_DB); return
    import dataproduct
    src = dataproduct.connect("fuel_history", path=DB)
    try:
        for t in ("my_prices", "wholesale_prices"):
            try:
                cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})")]
            except sqlite3.Error:
                cols = []
            if not cols:
                continue
            rows = src.execute(f"SELECT {','.join(cols)} FROM {t}").fetchall()
            if rows:
                ph = ",".join("?" * len(cols))
                con.executemany(
                    f"INSERT OR IGNORE INTO {t} ({','.join(cols)}) VALUES ({ph})",
                    [tuple(r) for r in rows])
        con.commit()
    except sqlite3.Error:
        # degrade gracefully (benchmark.db simply starts empty) but never silently:
        # an unnoticed failure here looks identical to "no legacy data".
        import applog
        applog.get("pricing_intelligence").exception(
            "legacy benchmark import failed — benchmark.db may be missing migrated "
            "rows (source: %s)", DB)
    finally:
        src.close()
    _MIGRATED.add(BENCHMARK_DB)


# ---------------------------------------------------------------- time bucketing
def bucket(date_iso, grain):
    """ISO date -> bucket key. day=YYYY-MM-DD, week=YYYY-Www, month=YYYY-MM."""
    try:
        d = datetime.date.fromisoformat(date_iso[:10])
    except (ValueError, TypeError):
        return date_iso
    if grain == "day":
        return d.isoformat()
    if grain == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    return f"{d.year}-{d.month:02d}"     # month


def bucket_period(period, date_iso, grain):
    """Period-CONSISTENT bucket key (FINDINGS pricing #1). The reports FILTER rows by
    the loaded `period` column, so they must BUCKET on the same dimension — otherwise
    an off-period-dated straggler loaded under a period (the routine case `anomaly.find`
    treats as non-anomalous) lands in a foreign date bucket and fragments the period's
    representative multi-supplier cell.

    - MONTH grain: the bucket IS the `period` itself, so every row loaded under a period
      (including a straggler dated in the prior/next month) groups into one cell.
    - DAY/WEEK grain: keep date-based buckets (the period's intra-period detail is the
      point), but CLAMP a row dated outside its period to the period's nearest boundary
      day so it never spawns a spurious out-of-period bucket.

    `period` may be None/blank (multi-period reports with no filter) — then fall back to
    the plain date bucket, which for month equals the row's own YYYY-MM."""
    if not period:
        return bucket(date_iso, grain)
    if grain == "month":
        return period
    # day/week: clamp a straggler into the period window [period-01, period-end]
    try:
        d = datetime.date.fromisoformat(date_iso[:10])
        y, m = int(period[:4]), int(period[5:7])
    except (ValueError, TypeError, IndexError):
        return bucket(date_iso, grain)
    lo = datetime.date(y, m, 1)
    hi = (datetime.date(y + (m == 12), (m % 12) + 1, 1) - datetime.timedelta(days=1))
    if d < lo:
        d = lo
    elif d > hi:
        d = hi
    return bucket(d.isoformat(), grain)


# ---------------------------------------------------------------- MY Prices intake
def load_my_prices(rows, replace_period=None, source="upload"):
    """rows: list of dicts/tuples (country, city, date, net_price[, product_group]).
    `source` tags where the prices came from (e.g. 'upload', 'portal:Q8').
    Returns count loaded."""
    con = connect()
    # Stamp/scope by the bound tenant (P2). OFF -> write_tenant()='default' (the column
    # DEFAULT) and scope_clause()=("",[]) so this is byte-identical to today; ON keeps
    # the antitrust-sensitive benchmark intra-tenant (docs/STRATEGY.md#security-compliance-evolution-plan-operating-as-a-multi-client-saas §7).
    # write_tenant() resolves FIRST so an ON tenant-less/owner write fails LOUD before
    # the DELETE replaces any rows.
    tid = tenancy.write_tenant()
    if replace_period:
        frag, tp = tenancy.scope_clause()
        # only this tenant replaces its OWN period's rows, never another tenant's.
        con.execute("DELETE FROM my_prices WHERE substr(date,1,7)=?" + frag,
                    [replace_period, *tp])
    n = 0
    for r in rows:
        if isinstance(r, dict):
            c, city, d, p = r["country"], r["city"], r["date"], r["net_price"]
            pg = r.get("product_group", "Diesel")
        else:
            c, city, d, p = r[0], r[1], r[2], r[3]
            pg = r[4] if len(r) > 4 else "Diesel"
        con.execute("INSERT OR REPLACE INTO my_prices (country,city,date,product_group,net_price,source,tenant_id)"
                    " VALUES (?,?,?,?,?,?,?)", (c, city.strip(), d, pg, float(p), source, tid))
        n += 1
    con.commit(); con.close()
    return n


# ---------------------------------------------------------------- advertised prices
def load_advertised_prices(rows, source="upload"):
    """rows: list of dicts/tuples (supplier, country, city, date, product_group,
    net_price) — the price a supplier ADVERTISED for that location/date. Prices are
    NET EUR/L, final (VAT excluded).

    HISTORICAL/APPEND-ONLY: unlike load_my_prices this NEVER wipes prior dates/periods.
    Re-uploading the same (supplier, country, city, date, product_group) corrects just
    THAT row via INSERT OR REPLACE; every other dated row is retained so a past invoice
    can always be checked against the advertised price that applied on its fill date.

    supplier/country normalize to upper-case (the module's convention). Returns count
    loaded."""
    con = connect()
    # Stamp by the bound tenant (P2). OFF -> write_tenant()='default' (the column
    # DEFAULT) so this is byte-identical to today; ON keeps the store intra-tenant and
    # a tenant-less/owner write fails LOUD here before any row is touched.
    tid = tenancy.write_tenant()
    n = 0
    for r in rows:
        if isinstance(r, dict):
            sup, c, city, d = r["supplier"], r["country"], r["city"], r["date"]
            pg = r.get("product_group") or "Diesel"
            p = r["net_price"]
        else:
            sup, c, city, d, pg, p = (r[0], r[1], r[2], r[3],
                                      (r[4] if len(r) > 4 and r[4] else "Diesel"),
                                      r[5] if len(r) > 5 else r[4])
        con.execute(
            "INSERT OR REPLACE INTO advertised_prices"
            " (supplier,country,city,date,product_group,net_price,source,tenant_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (sup.strip().upper(), c.strip().upper(), city.strip(), d, pg,
             money.f2(p), source, tid))
        n += 1
    con.commit(); con.close()
    return n


def add_advertised_price(supplier, country, city, date, net_price,
                         product_group="Diesel", source="manual"):
    """Single-row convenience wrapper over load_advertised_prices (for the R2 manual-
    entry form). NET EUR/L. Returns 1 on insert/replace."""
    return load_advertised_prices(
        [{"supplier": supplier, "country": country, "city": city, "date": date,
          "product_group": product_group, "net_price": net_price}], source=source)


def list_advertised_prices(supplier=None, country=None, limit=200):
    """Recent advertised-price rows for display (newest date first), optionally filtered
    by supplier/country. Scoped to the bound tenant via scope_clause() (owner sees all;
    inert OFF). The internal tenant_id plumbing is hidden from the returned dicts."""
    con = connect()
    where = "1=1"
    args = []
    if supplier:
        where += " AND supplier=?"; args.append(supplier.strip().upper())
    if country:
        where += " AND country=?"; args.append(country.strip().upper())
    frag, tp = tenancy.scope_clause()
    rows = [dict(r) for r in con.execute(
        f"SELECT * FROM advertised_prices WHERE {where}{frag}"
        " ORDER BY date DESC, supplier, city LIMIT ?", args + list(tp) + [int(limit)])]
    con.close()
    for d in rows:
        # tenant_id is internal P1 plumbing, not part of the exposed contract.
        d.pop(tenancy.TENANT_COLUMN, None)
    return rows


def load_wholesale(rows):
    con = connect(); n = 0
    # Stamp the bound tenant (P2). OFF -> 'default' (the column DEFAULT) = unchanged.
    tid = tenancy.write_tenant()
    for r in rows:
        c, d, p = (r["country"], r["date"], r["net_price"]) if isinstance(r, dict) else (r[0], r[1], r[2])
        src = r.get("source", "index") if isinstance(r, dict) else "index"
        con.execute("INSERT OR REPLACE INTO wholesale_prices (country,date,product_group,net_price,source,tenant_id)"
                    " VALUES (?,?,'Diesel',?,?,?)", (c, d, float(p), src, tid))
        n += 1
    con.commit(); con.close()
    return n


# ---------------------------------------------------------------- core: supplier NET grid
def supplier_grid(period=None, grain="month", product_group="Diesel"):
    """Volume-weighted supplier NET eff price per country/city/bucket.
    -> list of dicts: country, city, bucket, supplier, qty, net_eur_eff, eff_price."""
    # `transactions` is engine-owned; read it READ-ONLY from the product DB. The
    # benchmark tables aren't touched here, so no cross-DB join is needed — the
    # per-grain rebucket/aggregation below is pure Python.
    con = product_connect()
    where = "product_group=?"
    args = [product_group]
    if period:
        where += " AND period=?"; args.append(period)
    # P2: tenant-scope the aggregate — the clause goes in WHERE (before GROUP BY) so the
    # volume-weighted aggregate is computed over the current tenant's rows ONLY. OFF/owner
    # -> ("",[]) (unchanged); single-table read so tenant_id is unambiguous.
    frag, tp = tenancy.scope_clause()
    rows = con.execute(f"""SELECT country, station AS city, period, date, supplier,
        SUM(qty) qty, SUM(net_eur_eff) net FROM transactions
        WHERE {where}{frag} GROUP BY country, station, period, date, supplier""",
        args + list(tp)).fetchall()
    con.close()
    # rebucket by grain and re-aggregate (volume-weighted). Bucket on the PERIOD
    # dimension we filtered (bucket_period), not the raw date (FINDINGS pricing #1).
    agg = {}
    for r in rows:
        key = (r["country"], r["city"], bucket_period(r["period"], r["date"], grain), r["supplier"])
        a = agg.setdefault(key, [0.0, 0.0])
        a[0] += r["qty"]; a[1] += r["net"]
    out = []
    for (c, city, bk, sup), (q, net) in agg.items():
        out.append({"country": c, "city": city, "bucket": bk, "supplier": sup,
                    "qty": round(q, 1), "net_eur_eff": money.f2(net),     # currency
                    "eff_price": round(net / q, 4) if q else None})        # EUR/L (4dp)
    return out


# ---------------------------------------------------------------- margin baselines
def _my_price_lookup(con, country, city, date, pg="Diesel", tolerance=3):
    """Match cascade: exact -> +/-tol days same city -> city month avg -> country avg."""
    # P2: every my_prices read is tenant-scoped — the benchmark a tenant matches against
    # is its OWN prices only. OFF/owner -> ("",[]) (unchanged). The AVG aggregates take
    # the clause in WHERE so they average over the tenant's rows only.
    frag, tp = tenancy.scope_clause()
    row = con.execute("""SELECT net_price FROM my_prices
        WHERE country=? AND city=? AND date=? AND product_group=?""" + frag,
        [country, city, date, pg, *tp]).fetchone()
    if row: return row["net_price"], "exact"
    near = con.execute("""SELECT net_price, ABS(julianday(date)-julianday(?)) d
        FROM my_prices WHERE country=? AND city=? AND product_group=?""" + frag +
        " ORDER BY d LIMIT 1", [date, country, city, pg, *tp]).fetchone()
    if near and near["d"] is not None and near["d"] <= tolerance:
        return near["net_price"], "near-date"
    mavg = con.execute("""SELECT AVG(net_price) p FROM my_prices
        WHERE country=? AND city=? AND product_group=? AND substr(date,1,7)=substr(?,1,7)"""
        + frag, [country, city, pg, date, *tp]).fetchone()
    if mavg and mavg["p"] is not None: return mavg["p"], "city-avg"
    cavg = con.execute("""SELECT AVG(net_price) p FROM my_prices
        WHERE country=? AND product_group=? AND substr(date,1,7)=substr(?,1,7)""" + frag,
        [country, pg, date, *tp]).fetchone()
    if cavg and cavg["p"] is not None: return cavg["p"], "country-avg"
    return None, "no-benchmark"


def margin_report(period=None, grain="month", product_group="Diesel"):
    """For each supplier/country/city/bucket: effective NET, all three baselines,
    gap and EUR impact. -> list of row dicts + summary."""
    con = connect()
    grid = supplier_grid(period, grain, product_group)
    # pack average per country/city/bucket (across suppliers)
    pack = {}
    for g in grid:
        pack.setdefault((g["country"], g["city"], g["bucket"]), []).append((g["supplier"], g["eff_price"], g["qty"]))
    rows = []
    for g in grid:
        if g["eff_price"] is None:
            continue
        # representative date for the bucket = first day we can reconstruct; use bucket
        sample_date = _bucket_sample_date(g["bucket"], grain)
        my, conf = _my_price_lookup(con, g["country"], g["city"], sample_date, product_group)
        # pack: VOLUME-WEIGHTED average of OTHER suppliers same cell (FINDINGS #2).
        # A simple statistics.mean() let a 50 L outlier fill move the pack as much as a
        # 40,000 L fill — wrong whenever volumes differ. The qty is already carried as
        # the 3rd element of the pack tuples, so weight by it: Σother_net/Σother_qty,
        # reconstructing each other's net = price×qty. None when there are no others.
        others = [(p, q) for (s, p, q) in pack[(g["country"], g["city"], g["bucket"])]
                  if s != g["supplier"] and p is not None and q]
        other_qty = sum(q for (p, q) in others)
        pack_avg = round(sum(p * q for (p, q) in others) / other_qty, 4) if other_qty else None
        # wholesale (P2: tenant-scoped — averages the tenant's own index rows only;
        # OFF/owner -> ("",[]) unchanged). Clause in WHERE so the AVG is per-tenant.
        wfrag, wtp = tenancy.scope_clause()
        wh = con.execute("""SELECT AVG(net_price) p FROM wholesale_prices
            WHERE country=? AND product_group=? AND substr(date,1,7)=substr(?,1,7)""" + wfrag,
            [g["country"], product_group, sample_date, *wtp]).fetchone()
        # `is not None` (not a truthiness test): a legitimate benchmark price of
        # exactly 0.0 is a real price, NOT "no benchmark" — a falsy-zero guard would
        # drop the row from matched volume and suppress the gap. None == no benchmark.
        whole = round(wh["p"], 4) if wh and wh["p"] is not None else None
        gap_my = round(g["eff_price"] - my, 4) if my is not None else None
        rows.append({**g,
            "my_price": round(my, 4) if my is not None else None, "match": conf,
            "gap_vs_my": gap_my,
            "eur_impact": money.f2(gap_my * g["qty"]) if gap_my is not None else None,
            "pack_avg": pack_avg,
            "gap_vs_pack": round(g["eff_price"] - pack_avg, 4) if pack_avg is not None else None,
            "wholesale": whole,
            "margin_vs_wholesale": round(g["eff_price"] - whole, 4) if whole is not None else None})
    con.close()
    rows.sort(key=lambda r: r["eur_impact"] or -1e9, reverse=True)
    total_overpay = money.f2(sum(r["eur_impact"] for r in rows if r["eur_impact"] and r["eur_impact"] > 0))
    matched_vol = sum(r["qty"] for r in rows if r["my_price"] is not None)
    unmatched_vol = sum(r["qty"] for r in rows if r["my_price"] is None)
    summary = {"total_overpay": total_overpay, "rows": len(rows),
               "matched_litres": round(matched_vol), "unmatched_litres": round(unmatched_vol)}
    return rows, summary


def internal_benchmark(period=None, grain="month", product_group="Diesel"):
    """SELF-SOURCED competitor benchmark — built only from YOUR OWN multi-supplier
    purchases, no external/scraped data. For each country/city/bucket where you bought
    from at least one supplier, report the best (lowest) effective NET price you
    actually obtained, the spread, and the avoidable overpay vs that best.

    Compared per COUNTRY × period (apples-to-apples across suppliers, like the
    head-to-head view) — each supplier's volume-weighted effective price. Cells where
    two or more suppliers competed are the genuinely comparable ones; the overpay there
    is money you could have saved by routing volume to the cheaper supplier you were
    already using. Returns (rows sorted by overpay desc, summary)."""
    # `transactions` is engine-owned; read it READ-ONLY from the product DB. This
    # report uses only transactions (no benchmark join), so a single read-only
    # product connection suffices — the best-of aggregation is pure Python.
    con = product_connect()
    where = "product_group=?"; args = [product_group]
    if period:
        where += " AND period=?"; args.append(period)
    # P2: tenant-scope the aggregate (clause in WHERE before GROUP BY) — the self-sourced
    # benchmark is built from the tenant's OWN purchases only. OFF/owner -> ("",[]).
    frag, tp = tenancy.scope_clause()
    raw = con.execute(f"""SELECT country, period, date, supplier,
        SUM(qty) qty, SUM(net_eur_eff) net FROM transactions
        WHERE {where}{frag} GROUP BY country, period, date, supplier""",
        args + list(tp)).fetchall()
    con.close()
    agg = {}   # (country, bucket, supplier) -> [qty, net]
    # bucket on the PERIOD dimension we filtered (FINDINGS pricing #1) so an off-period
    # straggler groups into the period's multi-supplier cell, not a stray date bucket.
    for r in raw:
        k = (r["country"], bucket_period(r["period"], r["date"], grain), r["supplier"])
        a = agg.setdefault(k, [0.0, 0.0]); a[0] += r["qty"] or 0; a[1] += r["net"] or 0
    cells = {}
    for (country, bk, sup), (q, net) in agg.items():
        if q <= 0:
            continue
        cells.setdefault((country, bk), []).append({"supplier": sup, "qty": q, "net": net, "eff": net / q})
    rows, tot_overpay, tot_litres = [], 0.0, 0.0
    for (country, bk), sups in cells.items():
        best = min(sups, key=lambda s: s["eff"])
        litres = sum(s["qty"] for s in sups)
        spend = sum(s["net"] for s in sups)
        overpay = sum(s["qty"] * (s["eff"] - best["eff"]) for s in sups)
        rows.append({
            "country": country, "bucket": bk,
            "best_price": round(best["eff"], 4), "best_supplier": best["supplier"],
            "suppliers": len(sups), "your_avg": round(spend / litres, 4) if litres else None,
            "spread": round(max(s["eff"] for s in sups) - best["eff"], 4),
            "litres": round(litres, 1), "overpay_eur": money.f2(overpay)})  # currency
        tot_overpay += overpay; tot_litres += litres
    rows.sort(key=lambda r: r["overpay_eur"], reverse=True)
    # totals accumulated at full precision; the avoidable-overpay € is HALF_UP (money.f2)
    summary = {"total_overpay": money.f2(tot_overpay), "litres": round(tot_litres, 1),
               "cells": len(rows),
               "multi_supplier_cells": sum(1 for r in rows if r["suppliers"] > 1)}
    return rows, summary


def peer_benchmark(period=None, grain="month", product_group="Diesel", min_contributors=None):
    """PER-ENTITY vs PEER internal benchmark (M1) — fills the "no-benchmark" gap in the
    pricing grid using the pooled invoice data itself. For each (country, bucket) and
    each of OUR entities, compare the entity's effective NET EUR/L against the EQUAL-
    WEIGHT-PER-ENTITY MEDIAN of the OTHER entities in the same cell (the entity itself
    EXCLUDED). Where the entity pays ABOVE the peer median it is "addressable" spend.

    Equal-weight-per-entity (median of the others' effective €/L, NOT volume-weighted)
    is the robust, aggregated "cannot single out one entity" form. For exactly two other
    entities the median is the mean of the two.

    Min-cohort suppression: a cell with fewer than `min_contributors` OTHER entities
    (default PEER_MIN_CONTRIBUTORS) is SUPPRESSED — no peer figure is emitted (rendered
    "cohort too small") so no single entity can be singled out from the peer aggregate.

    Prices are NET EUR/L, final (VAT excluded, rebates applied). `transactions` is read
    READ-ONLY via the product boundary; per-litre carried at 3-4dp, EUR via money.f2.

    Returns (rows, summary):
      rows: [{entity, country, bucket, eff_price, qty, peers (count of OTHERS),
              peer_median, gap, addressable_eur, suppressed (bool)}], biggest
            addressable € first.
      summary: {total_addressable_eur, cells, suppressed_cells}
    """
    import statistics
    if min_contributors is None:
        min_contributors = PEER_MIN_CONTRIBUTORS
    # `transactions` is engine-owned; read it READ-ONLY from the product DB. Only
    # transactions are used (no benchmark join) so a single read-only connection
    # suffices — the per-entity rebucket/median is pure Python.
    con = product_connect()
    where = "product_group=?"; args = [product_group]
    if period:
        where += " AND period=?"; args.append(period)
    # ANTITRUST GATE (P2, docs/STRATEGY.md#security-compliance-evolution-plan-operating-as-a-multi-client-saas §7). The peer cohort MUST stay
    # intra-tenant: with the switch ON this clause (in WHERE, before GROUP BY) restricts the
    # aggregate to the CURRENT tenant's own entities, so a client benchmarks only against
    # ITSELF and can NEVER see another client's prices/entities. The min-contributor
    # suppression below still applies WITHIN the tenant. OFF/owner -> ("",[]): a single-
    # tenant install is byte-identical to today, and the audited owner analytics scope spans
    # all tenants (de-identified/aggregated only, per §7). Single-table read => tenant_id
    # unambiguous.
    frag, tp = tenancy.scope_clause()
    raw = con.execute(f"""SELECT entity, country, period, date,
        SUM(qty) qty, SUM(net_eur_eff) net FROM transactions
        WHERE {where}{frag} GROUP BY entity, country, period, date""",
        args + list(tp)).fetchall()
    con.close()
    # rebucket on the PERIOD dimension we filtered (FINDINGS pricing #1) — bucket_period,
    # NOT the raw date — so an off-period straggler groups into the period's cell.
    agg = {}   # (country, bucket, entity) -> [qty, net]
    for r in raw:
        k = (r["country"], bucket_period(r["period"], r["date"], grain), r["entity"])
        a = agg.setdefault(k, [0.0, 0.0]); a[0] += r["qty"] or 0; a[1] += r["net"] or 0
    cells = {}   # (country, bucket) -> [{entity, qty, eff}]
    for (country, bk, ent), (q, net) in agg.items():
        if q <= 0:
            continue
        # entity's effective NET €/L — money-summed spend / litres (full-precision div)
        cells.setdefault((country, bk), []).append(
            {"entity": ent, "qty": q, "eff": money.fsum([net]) / q})
    rows, tot_addr, suppressed_cells = [], 0.0, 0
    for (country, bk), ents in cells.items():
        for e in ents:
            others = [o for o in ents if o["entity"] != e["entity"]]
            n_peers = len(others)
            if n_peers < min_contributors:
                suppressed_cells += 1
                rows.append({
                    "entity": e["entity"], "country": country, "bucket": bk,
                    "eff_price": round(e["eff"], 4), "qty": round(e["qty"], 1),
                    "peers": n_peers, "peer_median": None, "gap": None,
                    "addressable_eur": None, "suppressed": True})
                continue
            peer_median = statistics.median(o["eff"] for o in others)
            gap = e["eff"] - peer_median
            # the entity pays ABOVE the peer median -> addressable (€ HALF_UP, money.f2)
            addressable = money.f2(gap * e["qty"]) if gap > 0 else 0.0
            tot_addr += addressable
            rows.append({
                "entity": e["entity"], "country": country, "bucket": bk,
                "eff_price": round(e["eff"], 4), "qty": round(e["qty"], 1),
                "peers": n_peers, "peer_median": round(peer_median, 4),
                "gap": round(gap, 4), "addressable_eur": addressable,
                "suppressed": False})
    rows.sort(key=lambda r: r["addressable_eur"] or -1e9, reverse=True)
    summary = {"total_addressable_eur": money.f2(tot_addr), "cells": len(cells),
               "suppressed_cells": suppressed_cells}
    return rows, summary


def peer_benchmark_workbook(period=None, grain="month", product_group="Diesel", path=None):
    """Excel of the per-entity-vs-peer benchmark (M1): each entity's eff NET €/L vs the
    peer median and the addressable € by country/bucket, biggest first. Suppressed cells
    (cohort too small) are shown as such. NET EUR/L, final (VAT excluded, rebates
    applied); € is money.f2 (HALF_UP)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    rows, summ = peer_benchmark(period, grain, product_group)
    path = path or f"{WORKDIR}/Peer_Benchmark_{grain}.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Per-entity vs peer"
    hdr = Font(bold=True, color="FFFFFF"); hf = PatternFill("solid", fgColor="0E5FA8")
    bad = PatternFill("solid", fgColor="FCE4E4")
    cols = ["entity", "country", "bucket", "eff_price", "peers", "peer_median",
            "gap", "qty", "addressable_eur", "suppressed"]
    ws.append([f"Per-entity vs peer (M1) — total addressable EUR {summ['total_addressable_eur']:,.0f} "
               f"across {summ['cells']} cells ({summ['suppressed_cells']} suppressed: cohort too small). "
               f"NET EUR/L, final (VAT excluded, rebates applied). Peer = equal-weight median of the "
               f"OTHER entities (the entity itself excluded)."])
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        ws.cell(2, c).font = hdr; ws.cell(2, c).fill = hf
    for r in rows:
        if r["suppressed"]:
            ws.append([r["entity"], r["country"], r["bucket"], r["eff_price"], r["peers"],
                       "cohort too small", "", r["qty"], "", "suppressed"])
        else:
            ws.append([r[k] for k in cols[:-1]] + [""])
            if r["addressable_eur"] and r["addressable_eur"] > 0:
                ws.cell(ws.max_row, 9).fill = bad
    ws.freeze_panes = "A3"; ws.sheet_view.showGridLines = False
    wb.save(path)
    return path


# ---------------------------------------------------------------- supplier reliability
def reliability_report(period=None, detail_limit=200):
    """SUPPLIER RELIABILITY — does a supplier INVOICE what it ADVERTISED?

    Customers see a price advertised on a supplier's portal and believe it's final; the
    invoice may charge MORE. We compare each invoiced fill's effective NET price against
    the advertised price that applied on that fill's date, per (supplier, country, city,
    product_group), and surface where a supplier overcharged.

    Prices everywhere are NET EUR/L, final (VAT excluded, rebates applied).
    Effective/INVOICED price = net_eur_eff / qty (the canonical definition).

    `period` ('YYYY-MM', optional) filters on the fill MONTH; None = all history.

    MATCHING / CARRY-FORWARD. Each fill is matched to the advertised price for the SAME
    (supplier, country, city, product_group): an exact-date row if present, else the most
    recent advertised row dated ON-OR-BEFORE the fill date (advertised prices aren't
    necessarily daily, so we carry the latest prior quote forward). A fill with no
    advertised reference on-or-before its date is UNMATCHED — excluded from the score and
    counted separately.

    Per matched fill: advertised, invoiced (= net_eur_eff/qty),
    delta = invoiced - advertised, overcharge_eur = max(0, delta) * qty. A small
    per-litre tolerance (OVERCHARGE_TOL_EUR_PER_L) keeps trivial rounding noise from
    being flagged as an overcharge.

    reliability_score = matched_fills_within_tolerance / matched_fills — a 0..1 ratio
    (1.0 = the supplier never invoiced materially above what it advertised; lower = more
    fills overcharged). Suppliers with no matched fills get a None score.

    Returns a dict:
      suppliers: [{supplier, matched_fills, overcharged_fills, unmatched_fills,
                   total_overcharge_eur, avg_delta_eur_per_l, reliability_score}]
                 sorted by total_overcharge_eur desc;
      detail:    overcharged fills (delta > tol) {supplier, country, city, date, product,
                 qty, advertised, invoiced, delta, overcharge_eur}, biggest first, capped
                 to detail_limit;
      summary:   {matched_fills, overcharged_fills, unmatched_fills, total_overcharge_eur}.
    """
    tol = OVERCHARGE_TOL_EUR_PER_L
    # --- invoiced fills: read transactions READ-ONLY via the product boundary. Same grain
    # the rest of the module uses (per supplier/country/station/date/product_group). The
    # app holds no writable handle to fuel_history.db (a stray write raises by design).
    con = product_connect()
    where = "1=1"
    args = []
    if period:
        where += " AND substr(date,1,7)=?"; args.append(period)
    # Respect the module's transactions tenant-scoping convention (clause in WHERE before
    # GROUP BY). OFF/owner -> ("",[]) unchanged; single-table read so tenant_id is
    # unambiguous.
    frag, tp = tenancy.scope_clause()
    fills = con.execute(
        f"""SELECT supplier, country, station AS city, date, product_group,
            SUM(qty) qty, SUM(net_eur_eff) net FROM transactions
            WHERE {where}{frag}
            GROUP BY supplier, country, station, date, product_group""",
        args + list(tp)).fetchall()
    con.close()

    # --- advertised prices (scoped) into an in-memory carry-forward index:
    #     (supplier, country, city, product_group) -> [(date, net_price)] sorted by date.
    bcon = connect()
    afrag, atp = tenancy.scope_clause()
    adv_rows = bcon.execute(
        "SELECT supplier, country, city, date, product_group, net_price"
        " FROM advertised_prices WHERE 1=1" + afrag + " ORDER BY date", atp).fetchall()
    bcon.close()
    # Match keys CASE/WHITESPACE-INSENSITIVELY on BOTH sides. The store upper-cases
    # supplier+country but leaves city/product_group as-entered, while the fills carry
    # the transactions' own casing (e.g. country "Poland", station "SUWALKI"); without
    # a shared normaliser an advertised "POLAND" would never match a "Poland" fill and
    # every fill would fall through as UNMATCHED. _k() normalises every key component.
    def _k(v):
        return (v or "").strip().upper()
    adv_index = {}
    for a in adv_rows:
        key = (_k(a["supplier"]), _k(a["country"]), _k(a["city"]), _k(a["product_group"]))
        adv_index.setdefault(key, []).append((a["date"], a["net_price"]))

    def _advertised_for(supplier, country, city, pg, fdate):
        """Exact-date advertised price, else the latest dated ON-OR-BEFORE fdate
        (carry-forward). None if nothing applies on-or-before the fill date."""
        series = adv_index.get((_k(supplier), _k(country), _k(city), _k(pg)))
        if not series:
            return None
        chosen = None
        for adate, price in series:           # ascending by date
            if adate == fdate:
                return price                   # exact match wins
            if adate <= fdate:
                chosen = price                 # most recent prior so far
            else:
                break                          # series sorted; no later row qualifies
        return chosen

    # --- per-fill comparison + per-supplier aggregation
    agg = {}   # supplier -> stats
    detail = []
    for f in fills:
        sup = f["supplier"]
        s = agg.setdefault(sup, {"matched": 0, "within_tol": 0, "overcharged": 0,
                                 "unmatched": 0, "overcharge_eur": 0.0,
                                 "delta_sum": 0.0})
        qty = f["qty"] or 0.0
        if qty <= 0:
            continue
        advertised = _advertised_for(sup, f["country"], f["city"],
                                     f["product_group"], f["date"])
        if advertised is None:
            s["unmatched"] += 1
            continue
        invoiced = f["net"] / qty               # effective NET EUR/L (full precision)
        delta = invoiced - advertised
        s["matched"] += 1
        s["delta_sum"] += delta
        if delta > tol:
            s["overcharged"] += 1
            oc = money.f2(delta * qty)          # EUR HALF_UP
            s["overcharge_eur"] += oc
            detail.append({
                "supplier": sup, "country": f["country"], "city": f["city"],
                "date": f["date"], "product": f["product_group"],
                "qty": round(qty, 1), "advertised": round(advertised, 4),
                "invoiced": round(invoiced, 4), "delta": round(delta, 4),
                "overcharge_eur": oc})
        else:
            s["within_tol"] += 1

    suppliers = []
    for sup, s in agg.items():
        matched = s["matched"]
        suppliers.append({
            "supplier": sup,
            "matched_fills": matched,
            "overcharged_fills": s["overcharged"],
            "unmatched_fills": s["unmatched"],
            "total_overcharge_eur": money.f2(s["overcharge_eur"]),
            "avg_delta_eur_per_l": round(s["delta_sum"] / matched, 4) if matched else None,
            # within-tolerance share of matched fills; None when nothing matched.
            "reliability_score": round(s["within_tol"] / matched, 4) if matched else None})
    suppliers.sort(key=lambda r: r["total_overcharge_eur"], reverse=True)

    detail.sort(key=lambda r: r["overcharge_eur"], reverse=True)
    detail = detail[:detail_limit]

    summary = {
        "matched_fills": sum(s["matched_fills"] for s in suppliers),
        "overcharged_fills": sum(s["overcharged_fills"] for s in suppliers),
        "unmatched_fills": sum(s["unmatched_fills"] for s in suppliers),
        "total_overcharge_eur": money.f2(sum(s["total_overcharge_eur"] for s in suppliers))}
    return {"suppliers": suppliers, "detail": detail, "summary": summary}


def adopt_internal_benchmark(period=None, grain="month", product_group="Diesel"):
    """Persist the best-of internal benchmark as the MY-Prices baseline (source
    'internal'), so the margin/gap columns measure every supplier against the best
    price you actually achieved. Returns the count loaded."""
    rows, _ = internal_benchmark(period, grain, product_group)
    myrows = [{"country": r["country"], "city": "(best-of)",
               "date": _bucket_sample_date(r["bucket"], grain),
               "net_price": r["best_price"], "product_group": product_group}
              for r in rows]
    return load_my_prices(myrows, source="internal")


def internal_benchmark_workbook(period=None, grain="month", product_group="Diesel", path=None):
    """Excel of the self-sourced benchmark + avoidable overpay, biggest first."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    rows, summ = internal_benchmark(period, grain, product_group)
    path = path or f"{WORKDIR}/Internal_Benchmark_{grain}.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Best-of benchmark"
    hdr = Font(bold=True, color="FFFFFF"); hf = PatternFill("solid", fgColor="0E5FA8")
    cols = ["country", "bucket", "suppliers", "best_supplier", "best_price",
            "your_avg", "spread", "litres", "overpay_eur"]
    ws.append([f"Self-sourced benchmark — total avoidable overpay EUR {summ['total_overpay']:,.0f} "
               f"across {summ['multi_supplier_cells']} multi-supplier cells (NET EUR/L, rebates applied)"])
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        ws.cell(2, c).font = hdr; ws.cell(2, c).fill = hf
    for r in rows:
        ws.append([r.get(k) for k in cols])
    ws.freeze_panes = "A3"; ws.sheet_view.showGridLines = False
    wb.save(path)
    return path


def _bucket_sample_date(bk, grain):
    if grain == "day":
        return bk
    if grain == "week":
        y, w = bk.split("-W")
        return datetime.date.fromisocalendar(int(y), int(w), 4).isoformat()  # Thursday
    return bk + "-15"   # month -> mid


def margin_trend(country, supplier, grain="month", product_group="Diesel"):
    """Time series of one supplier's price + all baselines in one country, for charts."""
    rows, _ = margin_report(None, grain, product_group)
    series = [r for r in rows if r["country"] == country and r["supplier"] == supplier]
    series.sort(key=lambda r: r["bucket"])
    return series


def export_excel(path=None, grain="month", product_group="Diesel"):
    """Daily/weekly/monthly grid + margin baselines, for your own pricing models."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.chart import LineChart, Reference
    path = path or f"{WORKDIR}/Pricing_Intel_{grain}.xlsx"
    wb = Workbook()
    hdr = Font(bold=True, color="FFFFFF"); hf = PatternFill("solid", fgColor="0E5FA8")
    bad = PatternFill("solid", fgColor="FCE4E4"); good = PatternFill("solid", fgColor="E5F3E0")
    # one sheet per grain so models can pick
    for gr in ("day", "week", "month"):
        rows, summ = margin_report(None, gr, product_group)
        ws = wb.create_sheet(gr.capitalize())
        cols = ["country","city","bucket","supplier","qty","eff_price","my_price","match",
                "gap_vs_my","eur_impact","pack_avg","gap_vs_pack","wholesale","margin_vs_wholesale"]
        ws.append(cols)
        for c in range(1, len(cols)+1):
            ws.cell(1, c).font = hdr; ws.cell(1, c).fill = hf
        for r in rows:
            ws.append([r.get(k) for k in cols])
            xl = ws.max_row
            if r.get("eur_impact") and r["eur_impact"] > 0:
                ws.cell(xl, 10).fill = bad
            elif r.get("eur_impact") and r["eur_impact"] < 0:
                ws.cell(xl, 10).fill = good
        ws.freeze_panes = "A2"
        for col, w in zip("ABCDEFGHIJKLMN", [10,16,10,8,9,9,9,10,9,10,9,10,9,12]):
            ws.column_dimensions[col].width = w
    # overview
    ov = wb.active; ov.title = "Overview"
    rows_m, summ_m = margin_report(None, "month", product_group)
    ov["A1"] = "Pricing intelligence — competitor NET price & margin"; ov["A1"].font = Font(bold=True, size=14)
    ov["A3"] = "All prices NET EUR/L, final (rebates applied, VAT excluded)."
    ov["A4"] = f"Total overpay vs MY Prices: EUR {summ_m['total_overpay']:,.2f}"
    ov["A5"] = f"Matched: {summ_m['matched_litres']:,} L | Unmatched (no benchmark): {summ_m['unmatched_litres']:,} L"
    ov["A7"] = "Baselines: gap_vs_my (your benchmark) | gap_vs_pack (vs other suppliers same city) | margin_vs_wholesale (true margin if index loaded)"
    ov["A8"] = "Sheets Day/Week/Month hold the same grid at each grain — combine freely for pricing models."
    wb.save(path)
    return path, summ_m

if __name__ == "__main__":
    import sys
    grain = sys.argv[1] if len(sys.argv) > 1 else "month"
    rows, summ = margin_report(grain=grain)
    print(f"=== Margin report ({grain}) ===  overpay vs MY: EUR {summ['total_overpay']:,.2f}  "
          f"| matched {summ['matched_litres']:,} L, unmatched {summ['unmatched_litres']:,} L\n")
    print(f"{'country':9}{'city':14}{'sup':7}{'eff':>8}{'MY':>8}{'gapMY':>8}{'pack':>8}{'whole':>8}{'EUR':>10}  match")
    for r in rows[:25]:
        print(f"{r['country'][:8]:9}{(r['city'] or '')[:13]:14}{r['supplier'][:6]:7}"
              f"{r['eff_price']:>8.3f}"
              f"{(r['my_price'] or 0):>8.3f}{(r['gap_vs_my'] or 0):>8.3f}"
              f"{(r['pack_avg'] or 0):>8.3f}{(r['wholesale'] or 0):>8.3f}"
              f"{(r['eur_impact'] or 0):>10.0f}  {r['match']}")
    if "--excel" in sys.argv:
        p, s = export_excel(grain=grain)
        print(f"\nExcel: {p}")
