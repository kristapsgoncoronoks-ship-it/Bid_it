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


def q_spend_trend(con):
    """Per-period fleet totals for the visual reports trends — one row per period,
    ALL product groups (not diesel-only). NET EUR basis: net_eur is the NET amount,
    vat_eur the VAT amount, litres = SUM(qty). Ordered oldest→newest so the line
    charts read left-to-right. Full precision (display-rounded at the boundary)."""
    return con.execute("""
        SELECT period, ROUND(SUM(qty),0) litres,
               ROUND(SUM(net_eur),2) net_eur, ROUND(SUM(vat_eur),2) vat_eur
        FROM transactions GROUP BY period ORDER BY period""").fetchall()


def q_price_trend_by_country(con):
    """Per-period × country effective NET €/L for the multi-series price trend — diesel
    only (apples-to-apples, matching the benchmark/savings basis). One row per
    (period, country); eff = SUM(net_eur_eff)/SUM(qty). Ordered by period then country.
    NET EUR/L basis."""
    return con.execute("""
        SELECT period, country, ROUND(SUM(qty),0) litres,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eff
        FROM transactions WHERE product_group='Diesel'
        GROUP BY period, country ORDER BY period, country""").fetchall()


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


def q_expense(con, period, entity=None):
    """Company expense / cost-allocation rollups over the validated transactions for
    `period` — a finance-facing NET / VAT / gross spend breakdown, per ENTITY (cost
    centre) and per VEHICLE, plus a per-product-group split. Read-only; NEVER raises
    (returns an empty-but-shaped dict on any error). When `entity` is given, all three
    rollups and the totals are restricted to that entity.

    BASIS: NET EUR, final (rebates applied) — `net_eur` is the NET amount; `vat_eur`
    is the VAT amount in EUR; gross_eur = net_eur + vat_eur. EUR figures are quantized
    HALF_UP (money.f2) at the boundary. Litres stay numeric (display-rounded). The
    effective NET €/L = SUM(net_eur_eff)/SUM(qty) (rebate-effective, divide-by-0 guarded).

    Returns a dict:
      by_entity:  [{entity, fuellings, n_vehicles, litres, net_eur, vat_eur, gross_eur}]
                  one row per entity, sorted by net_eur desc.
      by_vehicle: [{entity, vehicle, fuellings, litres, net_eur, vat_eur, gross_eur,
                    net_eur_l, n_countries}] one row per (entity, vehicle), net_eur desc.
      by_product: [{product_group, litres, net_eur, vat_eur, gross_eur}] sorted net_eur desc.
      totals:     {net_eur, vat_eur, gross_eur, litres, fuellings} over the filtered set."""
    empty = {"by_entity": [], "by_vehicle": [], "by_product": [], "totals":
             {"net_eur": 0.0, "vat_eur": 0.0, "gross_eur": 0.0, "litres": 0.0, "fuellings": 0}}
    try:
        w, p = ["period=?"], [period]
        if entity:
            w.append("entity=?"); p.append(entity)
        where_sql = " AND ".join(w)

        by_entity = []
        for r in con.execute(f"""
                SELECT entity, COUNT(*) fuellings, COUNT(DISTINCT vehicle) n_vehicles,
                       SUM(qty) litres, SUM(net_eur) net, SUM(vat_eur) vat
                FROM transactions WHERE {where_sql}
                GROUP BY entity ORDER BY net DESC""", p).fetchall():
            net = money.f2(r["net"] or 0); vat = money.f2(r["vat"] or 0)
            by_entity.append({"entity": r["entity"], "fuellings": r["fuellings"],
                              "n_vehicles": r["n_vehicles"], "litres": r["litres"] or 0.0,
                              "net_eur": net, "vat_eur": vat, "gross_eur": money.f2(net + vat)})

        by_vehicle = []
        for r in con.execute(f"""
                SELECT entity, vehicle, COUNT(*) fuellings, SUM(qty) litres,
                       SUM(net_eur) net, SUM(vat_eur) vat, SUM(net_eur_eff) net_eff,
                       COUNT(DISTINCT country) n_countries
                FROM transactions WHERE {where_sql}
                GROUP BY entity, vehicle ORDER BY net DESC""", p).fetchall():
            net = money.f2(r["net"] or 0); vat = money.f2(r["vat"] or 0)
            litres = r["litres"] or 0.0
            net_eur_l = (r["net_eff"] or 0.0) / litres if litres else 0.0
            by_vehicle.append({"entity": r["entity"], "vehicle": r["vehicle"],
                               "fuellings": r["fuellings"], "litres": litres,
                               "net_eur": net, "vat_eur": vat, "gross_eur": money.f2(net + vat),
                               "net_eur_l": net_eur_l, "n_countries": r["n_countries"]})

        by_product = []
        for r in con.execute(f"""
                SELECT product_group, SUM(qty) litres, SUM(net_eur) net, SUM(vat_eur) vat
                FROM transactions WHERE {where_sql}
                GROUP BY product_group ORDER BY net DESC""", p).fetchall():
            net = money.f2(r["net"] or 0); vat = money.f2(r["vat"] or 0)
            by_product.append({"product_group": r["product_group"], "litres": r["litres"] or 0.0,
                               "net_eur": net, "vat_eur": vat, "gross_eur": money.f2(net + vat)})

        t = con.execute(f"""
                SELECT COUNT(*) fuellings, SUM(qty) litres, SUM(net_eur) net, SUM(vat_eur) vat
                FROM transactions WHERE {where_sql}""", p).fetchone()
        tnet = money.f2(t["net"] or 0); tvat = money.f2(t["vat"] or 0)
        totals = {"net_eur": tnet, "vat_eur": tvat, "gross_eur": money.f2(tnet + tvat),
                  "litres": t["litres"] or 0.0, "fuellings": t["fuellings"] or 0}

        return {"by_entity": by_entity, "by_vehicle": by_vehicle,
                "by_product": by_product, "totals": totals}
    except Exception:
        return empty


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


def q_ledger(con, period, entity=None):
    """Transaction-level accounting ledger over the validated transactions for `period`
    — the decision-free first cut of an ERP/SAF-T export: one row per transaction, clean
    enough for finance to derive any journal or import into Xero/QuickBooks/DATEV/a
    spreadsheet. Read-only; NEVER raises (returns [] on any error). When `entity` is
    given, restricts to that entity.

    BASIS: NET EUR, final (VAT excluded, rebates applied). `net_eur` is the NET amount,
    `vat_eur` the VAT amount in EUR, and gross = net + VAT (no separate gross column is
    stored, so it is derived). Local-currency figures mirror the same: gross_local =
    net_local + vat_local. EUR figures are quantized HALF_UP (money.f2). vat_rate_pct is
    the implied VAT rate (100*vat_eur/net_eur, 1 dp), or 0.0 when net_eur is 0.

    Rows (one per transaction), sorted by (date, entity, supplier):
      {date, period, entity, supplier, country, vehicle, station, product, product_group,
       qty, currency, net_local, vat_local, gross_local, net_eur, vat_eur, gross_eur,
       vat_rate_pct, note}."""
    try:
        w, p = ["period=?"], [period]
        if entity:
            w.append("entity=?"); p.append(entity)
        where_sql = " AND ".join(w)
        out = []
        for r in con.execute(f"""
                SELECT date, period, entity, supplier, country, vehicle, station,
                       product, product_group, qty, currency, net_local, vat_local,
                       net_eur, vat_eur, note
                FROM transactions WHERE {where_sql}
                ORDER BY date, entity, supplier""", p).fetchall():
            net_local = r["net_local"] or 0.0
            vat_local = r["vat_local"] or 0.0
            net_eur = money.f2(r["net_eur"] or 0)
            vat_eur = money.f2(r["vat_eur"] or 0)
            vat_rate_pct = round(100.0 * vat_eur / net_eur, 1) if net_eur else 0.0
            out.append({"date": r["date"], "period": r["period"], "entity": r["entity"],
                        "supplier": r["supplier"], "country": r["country"],
                        "vehicle": r["vehicle"], "station": r["station"],
                        "product": r["product"], "product_group": r["product_group"],
                        "qty": r["qty"] or 0.0, "currency": r["currency"],
                        "net_local": net_local, "vat_local": vat_local,
                        "gross_local": net_local + vat_local,
                        "net_eur": net_eur, "vat_eur": vat_eur,
                        "gross_eur": money.f2(net_eur + vat_eur),
                        "vat_rate_pct": vat_rate_pct, "note": r["note"]})
        return out
    except Exception:
        return []
