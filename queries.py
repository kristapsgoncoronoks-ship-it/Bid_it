"""
QUERIES - read-only aggregations over fuel_history.db's transactions table.

Pure functions: each takes an open connection (or a request.args MultiDict) and
returns rows/dicts. Kept separate from app.py so the web layer stays small and
these are easy to test and reuse (the web routes and reports both build on them).
All money is NET EUR; effective price = net_eur_eff / qty.
"""


def q_periods(con):
    return [r[0] for r in con.execute("SELECT DISTINCT period FROM transactions ORDER BY period DESC")]


def q_filters(con):
    return {
        "suppliers": [r[0] for r in con.execute("SELECT DISTINCT supplier FROM transactions ORDER BY 1")],
        "countries": [r[0] for r in con.execute("SELECT DISTINCT country FROM transactions ORDER BY 1")],
        "products":  [r[0] for r in con.execute("SELECT DISTINCT product_group FROM transactions ORDER BY 1")],
        "stations":  [r[0] for r in con.execute(
            "SELECT DISTINCT station FROM transactions WHERE station<>'' ORDER BY 1")],
        "periods":   q_periods(con),
    }


def where(args, period=None):
    """Build a parameterized WHERE from report filters.

    `args` is a request.args MultiDict. supplier / country / station accept
    MULTIPLE values (rendered as `col IN (?,...)`); period / product are single;
    date_from / date_to bound the `date` column. `period` overrides args["period"]
    so the route can supply a resolved default (latest month)."""
    w, p = ["1=1"], []
    period = period if period is not None else args.get("period")
    if period and period != "ALL": w.append("period=?"); p.append(period)
    prod = args.get("product")
    if prod and prod != "ALL": w.append("product_group=?"); p.append(prod)
    for col, key in (("supplier", "supplier"), ("country", "country"), ("station", "station")):
        vals = [x for x in args.getlist(key) if x and x != "ALL"]
        if vals:
            w.append(f"{col} IN ({','.join('?' * len(vals))})"); p += vals
    if args.get("date_from"): w.append("date>=?"); p.append(args.get("date_from"))
    if args.get("date_to"):   w.append("date<=?"); p.append(args.get("date_to"))
    return " AND ".join(w), p


def q_compare(con, args, period=None):
    w, p = where(args, period)
    return con.execute(f"""
        SELECT supplier, country, product_group,
               ROUND(SUM(qty),0) litres, ROUND(SUM(net_eur),2) net_eur,
               ROUND(SUM(vat_eur),2) vat_eur,
               ROUND(SUM(net_eur)/NULLIF(SUM(qty),0),4) eur_l_doc,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eur_l_eff
        FROM transactions WHERE {w}
        GROUP BY supplier, country, product_group ORDER BY eur_l_eff""", p).fetchall()


def q_compare_totals(con, args, period=None):
    """Grand totals for the filtered set (one row), for the report summary."""
    w, p = where(args, period)
    return con.execute(f"""
        SELECT ROUND(SUM(qty),0) litres, ROUND(SUM(net_eur),2) net_eur,
               ROUND(SUM(vat_eur),2) vat_eur, COUNT(*) lines,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eur_l_eff
        FROM transactions WHERE {w}""", p).fetchone()


def q_benchmark(con, period):
    return con.execute("""
        SELECT supplier, country, ROUND(SUM(qty),0) litres,
               ROUND(SUM(net_eur)/NULLIF(SUM(qty),0),4) doc,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eff
        FROM transactions WHERE period=? AND product_group='Diesel'
        GROUP BY supplier, country ORDER BY eff""", (period,)).fetchall()


def q_kpis(con, period):
    return con.execute("""
        SELECT ROUND(SUM(net_eur),0) net, ROUND(SUM(vat_eur),0) vat,
               ROUND(SUM(net_eur)+SUM(vat_eur),0) gross,
               (SELECT ROUND(SUM(qty),0) FROM transactions WHERE period=? AND product_group='Diesel') litres,
               (SELECT ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) FROM transactions WHERE period=? AND product_group='Diesel') eurl
        FROM transactions WHERE period=?""", (period, period, period)).fetchone()


def q_trend(con):
    return con.execute("""
        SELECT period, ROUND(SUM(qty),0) litres,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eurl
        FROM transactions WHERE product_group='Diesel' GROUP BY period ORDER BY period""").fetchall()


def q_headtohead(con, period):
    rows = con.execute("""
        SELECT date, country, supplier, SUM(qty) q, SUM(net_eur_eff) e
        FROM transactions WHERE product_group='Diesel' AND period=?
        GROUP BY date, country, supplier""", (period,)).fetchall()
    g = {}
    for r in rows:
        g.setdefault((r["date"], r["country"]), {})[r["supplier"]] = (r["q"], r["e"])
    out = []
    for (d, c), bysup in sorted(g.items()):
        if len(bysup) < 2: continue
        prices = {s: e / qy for s, (qy, e) in bysup.items()}
        cheap = min(prices, key=prices.get)
        over = sum(qy * (prices[s] - prices[cheap]) for s, (qy, e) in bysup.items())
        out.append({"date": d, "country": c,
                    "prices": " | ".join(f"{s} {prices[s]:.4f}" for s in sorted(prices)),
                    "cheapest": cheap, "spread": round(max(prices.values()) - min(prices.values()), 4),
                    "litres": round(sum(v[0] for v in bysup.values())),
                    "overpay": round(over, 2)})
    return out


def q_entities(con, period):
    return con.execute("""SELECT entity, country, ROUND(SUM(net_eur),2) net,
        ROUND(SUM(vat_eur),2) vat, ROUND(SUM(net_eur)+SUM(vat_eur),2) gross
        FROM transactions WHERE period=? GROUP BY entity, country ORDER BY entity""", (period,)).fetchall()


def q_stations(con, period):
    return con.execute("""SELECT supplier, country, station, ROUND(SUM(qty),0) litres,
        ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eurl
        FROM transactions WHERE period=? AND product_group='Diesel'
        GROUP BY supplier, country, station HAVING SUM(qty)>=300 ORDER BY eurl""", (period,)).fetchall()


def q_savings(con, period):
    """Avoidable overpay = for each day+country where 2+ suppliers fueled diesel,
    litres x (this supplier's eff €/L − the cheapest rival's). Attributed to the
    country and the supplier that charged the premium. NET EUR/L basis."""
    rows = con.execute("""
        SELECT date, country, supplier, SUM(qty) q, SUM(net_eur_eff) e
        FROM transactions WHERE product_group='Diesel' AND period=?
        GROUP BY date, country, supplier""", (period,)).fetchall()
    g = {}
    for r in rows:
        if r["q"]:
            g.setdefault((r["date"], r["country"]), {})[r["supplier"]] = (r["q"], r["e"])
    total, by_country, by_supplier = 0.0, {}, {}
    for (_d, c), bysup in g.items():
        if len(bysup) < 2: continue
        prices = {s: e / qy for s, (qy, e) in bysup.items()}
        cheap = min(prices.values())
        for s, (qy, e) in bysup.items():
            over = qy * (prices[s] - cheap)
            if over <= 0: continue
            total += over
            by_country[c] = by_country.get(c, 0) + over
            by_supplier[s] = by_supplier.get(s, 0) + over
    return {"total": round(total, 2),
            "by_country": sorted(by_country.items(), key=lambda x: -x[1]),
            "by_supplier": sorted(by_supplier.items(), key=lambda x: -x[1])}
