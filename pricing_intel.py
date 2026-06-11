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
import os, sqlite3, datetime, statistics

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/fuel_history.db"


def connect():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS my_prices (
        country TEXT, city TEXT, date TEXT, product_group TEXT DEFAULT 'Diesel',
        net_price REAL, source TEXT DEFAULT 'upload',
        PRIMARY KEY (country, city, date, product_group));
    CREATE TABLE IF NOT EXISTS wholesale_prices (
        country TEXT, date TEXT, product_group TEXT DEFAULT 'Diesel',
        net_price REAL, source TEXT,
        PRIMARY KEY (country, date, product_group));
    CREATE INDEX IF NOT EXISTS ix_myp ON my_prices(country, city, date);
    CREATE INDEX IF NOT EXISTS ix_whp ON wholesale_prices(country, date);
    """)
    return con


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


# ---------------------------------------------------------------- MY Prices intake
def load_my_prices(rows, replace_period=None):
    """rows: list of dicts/tuples (country, city, date, net_price[, product_group]).
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
        con.execute("INSERT OR REPLACE INTO my_prices (country,city,date,product_group,net_price)"
                    " VALUES (?,?,?,?,?)", (c, city.strip(), d, pg, float(p)))
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
    con = connect()
    where = "product_group=?"
    args = [product_group]
    if period:
        where += " AND period=?"; args.append(period)
    rows = con.execute(f"""SELECT country, station AS city, date, supplier,
        SUM(qty) qty, SUM(net_eur_eff) net FROM transactions
        WHERE {where} GROUP BY country, station, date, supplier""", args).fetchall()
    con.close()
    # rebucket by grain and re-aggregate (volume-weighted)
    agg = {}
    for r in rows:
        key = (r["country"], r["city"], bucket(r["date"], grain), r["supplier"])
        a = agg.setdefault(key, [0.0, 0.0])
        a[0] += r["qty"]; a[1] += r["net"]
    out = []
    for (c, city, bk, sup), (q, net) in agg.items():
        out.append({"country": c, "city": city, "bucket": bk, "supplier": sup,
                    "qty": round(q, 1), "net_eur_eff": round(net, 2),
                    "eff_price": round(net / q, 4) if q else None})
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
    if mavg and mavg["p"]: return mavg["p"], "city-avg"
    cavg = con.execute("""SELECT AVG(net_price) p FROM my_prices
        WHERE country=? AND product_group=? AND substr(date,1,7)=substr(?,1,7)""",
        (country, pg, date)).fetchone()
    if cavg and cavg["p"]: return cavg["p"], "country-avg"
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
        # pack: average of OTHER suppliers same cell
        others = [p for (s, p, q) in pack[(g["country"], g["city"], g["bucket"])]
                  if s != g["supplier"] and p is not None]
        pack_avg = round(statistics.mean(others), 4) if others else None
        # wholesale
        wh = con.execute("""SELECT AVG(net_price) p FROM wholesale_prices
            WHERE country=? AND product_group=? AND substr(date,1,7)=substr(?,1,7)""",
            (g["country"], product_group, sample_date)).fetchone()
        whole = round(wh["p"], 4) if wh and wh["p"] else None
        gap_my = round(g["eff_price"] - my, 4) if my else None
        rows.append({**g,
            "my_price": round(my, 4) if my else None, "match": conf,
            "gap_vs_my": gap_my,
            "eur_impact": round(gap_my * g["qty"], 2) if gap_my is not None else None,
            "pack_avg": pack_avg,
            "gap_vs_pack": round(g["eff_price"] - pack_avg, 4) if pack_avg else None,
            "wholesale": whole,
            "margin_vs_wholesale": round(g["eff_price"] - whole, 4) if whole else None})
    con.close()
    rows.sort(key=lambda r: r["eur_impact"] or -1e9, reverse=True)
    total_overpay = round(sum(r["eur_impact"] for r in rows if r["eur_impact"] and r["eur_impact"] > 0), 2)
    matched_vol = sum(r["qty"] for r in rows if r["my_price"])
    unmatched_vol = sum(r["qty"] for r in rows if not r["my_price"])
    summary = {"total_overpay": total_overpay, "rows": len(rows),
               "matched_litres": round(matched_vol), "unmatched_litres": round(unmatched_vol)}
    return rows, summary


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
