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
] + tenancy.tenant_column_ddls([
    # P1 multi-tenancy (schema plumbing only): stamp the app-owned benchmark price
    # tables with a tenant_id; existing rows backfill to DEFAULT_TENANT_ID via the
    # column DEFAULT, new rows default too. NO query reads this column yet (the
    # `multitenant` switch is OFF and scope_clause is unwired until P2). This is
    # also exactly what P2 will use to keep the antitrust-sensitive benchmark
    # intra-tenant (docs/SECURITY_COMPLIANCE_PLAN.md §7). APPEND-ONLY — keep at END.
    "my_prices", "wholesale_prices",
])


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
    if replace_period:
        con.execute("DELETE FROM my_prices WHERE substr(date,1,7)=?", (replace_period,))
    n = 0
    for r in rows:
        if isinstance(r, dict):
            c, city, d, p = r["country"], r["city"], r["date"], r["net_price"]
            pg = r.get("product_group", "Diesel")
        else:
            c, city, d, p = r[0], r[1], r[2], r[3]
            pg = r[4] if len(r) > 4 else "Diesel"
        con.execute("INSERT OR REPLACE INTO my_prices (country,city,date,product_group,net_price,source)"
                    " VALUES (?,?,?,?,?,?)", (c, city.strip(), d, pg, float(p), source))
        n += 1
    con.commit(); con.close()
    return n


def load_wholesale(rows):
    con = connect(); n = 0
    for r in rows:
        c, d, p = (r["country"], r["date"], r["net_price"]) if isinstance(r, dict) else (r[0], r[1], r[2])
        src = r.get("source", "index") if isinstance(r, dict) else "index"
        con.execute("INSERT OR REPLACE INTO wholesale_prices (country,date,product_group,net_price,source)"
                    " VALUES (?,?,'Diesel',?,?)", (c, d, float(p), src))
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
    rows = con.execute(f"""SELECT country, station AS city, period, date, supplier,
        SUM(qty) qty, SUM(net_eur_eff) net FROM transactions
        WHERE {where} GROUP BY country, station, period, date, supplier""", args).fetchall()
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
    row = con.execute("""SELECT net_price FROM my_prices
        WHERE country=? AND city=? AND date=? AND product_group=?""",
        (country, city, date, pg)).fetchone()
    if row: return row["net_price"], "exact"
    near = con.execute("""SELECT net_price, ABS(julianday(date)-julianday(?)) d
        FROM my_prices WHERE country=? AND city=? AND product_group=?
        ORDER BY d LIMIT 1""", (date, country, city, pg)).fetchone()
    if near and near["d"] is not None and near["d"] <= tolerance:
        return near["net_price"], "near-date"
    mavg = con.execute("""SELECT AVG(net_price) p FROM my_prices
        WHERE country=? AND city=? AND product_group=? AND substr(date,1,7)=substr(?,1,7)""",
        (country, city, pg, date)).fetchone()
    if mavg and mavg["p"] is not None: return mavg["p"], "city-avg"
    cavg = con.execute("""SELECT AVG(net_price) p FROM my_prices
        WHERE country=? AND product_group=? AND substr(date,1,7)=substr(?,1,7)""",
        (country, pg, date)).fetchone()
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
        # wholesale
        wh = con.execute("""SELECT AVG(net_price) p FROM wholesale_prices
            WHERE country=? AND product_group=? AND substr(date,1,7)=substr(?,1,7)""",
            (g["country"], product_group, sample_date)).fetchone()
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
    raw = con.execute(f"""SELECT country, period, date, supplier,
        SUM(qty) qty, SUM(net_eur_eff) net FROM transactions
        WHERE {where} GROUP BY country, period, date, supplier""", args).fetchall()
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
    raw = con.execute(f"""SELECT entity, country, period, date,
        SUM(qty) qty, SUM(net_eur_eff) net FROM transactions
        WHERE {where} GROUP BY entity, country, period, date""", args).fetchall()
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
