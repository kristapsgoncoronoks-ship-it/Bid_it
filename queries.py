"""
QUERIES - read-only aggregations over fuel_history.db's transactions table.

Pure functions: each takes an open connection (or a request.args MultiDict) and
returns rows/dicts. Kept separate from app.py so the web layer stays small and
these are easy to test and reuse (the web routes and reports both build on them).
All money is NET EUR; effective price = net_eur_eff / qty.
"""
import money


def q_periods(con):
    return [r[0] for r in con.execute("SELECT DISTINCT period FROM transactions ORDER BY period DESC")]


def q_filters(con):
    return {
        "entities":  [r[0] for r in con.execute(
            "SELECT DISTINCT entity FROM transactions WHERE entity<>'' ORDER BY 1")],
        "suppliers": [r[0] for r in con.execute("SELECT DISTINCT supplier FROM transactions ORDER BY 1")],
        "countries": [r[0] for r in con.execute("SELECT DISTINCT country FROM transactions ORDER BY 1")],
        "products":  [r[0] for r in con.execute("SELECT DISTINCT product_group FROM transactions ORDER BY 1")],
        "stations":  [r[0] for r in con.execute(
            "SELECT DISTINCT station FROM transactions WHERE station<>'' ORDER BY 1")],
        "periods":   q_periods(con),
    }


def where(args, period=None):
    """Build a parameterized WHERE from report filters.

    `args` is a request.args MultiDict. entity (client) / supplier / country /
    station accept MULTIPLE values (rendered as `col IN (?,...)`); period / product
    are single; date_from / date_to bound the `date` column. `period` overrides
    args["period"] so the route can supply a resolved default (latest month)."""
    w, p = ["1=1"], []
    period = period if period is not None else args.get("period")
    if period and period != "ALL": w.append("period=?"); p.append(period)
    prod = args.get("product")
    if prod and prod != "ALL": w.append("product_group=?"); p.append(prod)
    for col, key in (("entity", "entity"), ("supplier", "supplier"),
                     ("country", "country"), ("station", "station")):
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
                    "overpay": money.f2(over)})       # currency -> HALF_UP
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


def _savings_lines(con, period):
    """Canonical avoidable-overpay loop, emitting ONE record per (supplier, date,
    country) where that supplier charged a premium vs the cheapest same-day,
    same-country diesel rival. Full precision (no rounding) — callers quantize at
    the boundary. Shared source of truth for q_savings (the aggregate) and
    q_savings_lines (the detail packet). NET EUR/L basis.

    Each record: (supplier, date, country, litres, eur_l, cheapest_eur_l,
    cheapest_supplier, delta_eur_l, overpay_eur) with overpay_eur at full precision."""
    rows = con.execute("""
        SELECT date, country, supplier, SUM(qty) q, SUM(net_eur_eff) e
        FROM transactions WHERE product_group='Diesel' AND period=?
        GROUP BY date, country, supplier""", (period,)).fetchall()
    g = {}
    for r in rows:
        if r["q"]:
            g.setdefault((r["date"], r["country"]), {})[r["supplier"]] = (r["q"], r["e"])
    for (d, c), bysup in g.items():
        if len(bysup) < 2: continue
        prices = {s: e / qy for s, (qy, e) in bysup.items()}
        cheap_price = min(prices.values())
        cheap_sup = min(prices, key=prices.get)
        for s, (qy, e) in bysup.items():
            delta = prices[s] - cheap_price
            over = qy * delta
            if over <= 0: continue
            yield {"supplier": s, "date": d, "country": c, "litres": qy,
                   "eur_l": prices[s], "cheapest_eur_l": cheap_price,
                   "cheapest_supplier": cheap_sup, "delta_eur_l": delta,
                   "overpay_eur": over}


def q_savings(con, period):
    """Avoidable overpay = for each day+country where 2+ suppliers fueled diesel,
    litres x (this supplier's eff €/L − the cheapest rival's). Attributed to the
    country and the supplier that charged the premium. NET EUR/L basis."""
    total, by_country, by_supplier = 0.0, {}, {}
    for ln in _savings_lines(con, period):
        over = ln["overpay_eur"]
        total += over
        by_country[ln["country"]] = by_country.get(ln["country"], 0) + over
        by_supplier[ln["supplier"]] = by_supplier.get(ln["supplier"], 0) + over
    # `total` accumulated at full precision; quantize the final overpay figure
    # HALF_UP (money.f2), consistent with the VAT money basis. by_country/by_supplier
    # are charted at integer-EUR display, so left at full precision.
    return {"total": money.f2(total),
            "by_country": sorted(by_country.items(), key=lambda x: -x[1]),
            "by_supplier": sorted(by_supplier.items(), key=lambda x: -x[1])}


def q_savings_lines(con, period, supplier=None):
    """DETAIL version of q_savings: one row per (supplier, date, country) where that
    supplier overpaid vs the cheapest same-day, same-country diesel rival — the
    fuelling-day evidence behind the aggregate. Same algorithm/basis as q_savings
    (shares _savings_lines). Read-only; never raises (returns [] on any error).

    Rows: {supplier, date, country, litres, eur_l, cheapest_eur_l, cheapest_supplier,
    delta_eur_l, overpay_eur}. overpay_eur is quantized HALF_UP (money.f2); the per-
    supplier sum of overpay_eur reconciles with q_savings' by_supplier (modulo the
    final HALF_UP rounding). Sorted by overpay_eur desc. `supplier` filters to one.

    This is a price-COMPETITIVENESS / negotiation review (supplier X charged €Y more
    than the cheapest same-day, same-country rival) — NOT a contractual claim-back."""
    try:
        out = []
        for ln in _savings_lines(con, period):
            if supplier is not None and ln["supplier"] != supplier:
                continue
            out.append({"supplier": ln["supplier"], "date": ln["date"],
                        "country": ln["country"], "litres": ln["litres"],
                        "eur_l": ln["eur_l"], "cheapest_eur_l": ln["cheapest_eur_l"],
                        "cheapest_supplier": ln["cheapest_supplier"],
                        "delta_eur_l": ln["delta_eur_l"],
                        "overpay_eur": money.f2(ln["overpay_eur"])})
        out.sort(key=lambda x: -x["overpay_eur"])
        return out
    except Exception:
        return []
