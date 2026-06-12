"""
CONTRACT-COMPLIANCE AUDITOR — catch money you're already losing, from invoice data
you already have. Compares every invoiced fuel line against the supplier's structured
contract terms (supplier_master.supplier_discounts) and flags:

  • short/missed discount — the rebate actually applied (net_eur − net_eur_eff, per
    litre) is below the contracted EUR/L for that supplier/country/station/product.
  • over ceiling — the effective NET price exceeds a contracted maximum EUR/L.

Each flag carries the recoverable EUR (the shortfall × litres) so you can chase it.
Pure data, offline, no external calls.

Rules use SQL-LIKE patterns for country/station ('%' = any). Use expected_discount
for rebate-style suppliers (where net_eur_eff < net_eur, e.g. Q8/Port One) and
max_net for suppliers whose discount is baked into the doc price.
"""
import os, re, sqlite3

import supplier_master
import db_tuning

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/fuel_history.db"
TOLERANCE = float(os.environ.get("AUDIT_TOLERANCE_EUR_L", "0.005"))   # 0.5 cent/L slack


def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)
    return con

def _like(value, pattern):
    """SQL-LIKE match (%, _) case-insensitive; '%' matches anything including empty."""
    if pattern in (None, "%", ""):
        return True
    rx = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
    return re.match("^" + rx + "$", value or "", re.IGNORECASE) is not None


def audit(period=None, tolerance=TOLERANCE):
    """Returns (flags sorted by recoverable EUR desc, summary). A flag = one
    supplier/country/station/period group that breaches a contract rule."""
    rules = supplier_master.discount_rules()
    con = _con()
    where, args = "1=1", []
    if period:
        where += " AND period=?"; args.append(period)
    groups = con.execute(f"""SELECT supplier, country, station, product_group, period,
        SUM(qty) qty, SUM(net_eur) net, SUM(net_eur_eff) eff
        FROM transactions WHERE {where}
        GROUP BY supplier, country, station, product_group, period""", args).fetchall()
    con.close()

    flags, total, by_sup = [], 0.0, {}
    for g in groups:
        qty = g["qty"] or 0
        if qty <= 0 or g["net"] is None or g["eff"] is None:
            continue
        applied = (g["net"] - g["eff"]) / qty        # rebate actually applied, EUR/L
        eff_l = g["eff"] / qty                        # effective NET price, EUR/L
        for r in rules:
            if r["supplier"] != (g["supplier"] or "").upper():
                continue
            if r["product_group"] and r["product_group"] != g["product_group"]:
                continue
            if not _like(g["country"], r["country"]) or not _like(g["station"], r["station_like"]):
                continue
            def _flag(issue, expected, actual, recover):
                nonlocal total
                rec = round(recover, 2)
                if rec <= 0:
                    return
                flags.append({"supplier": g["supplier"], "country": g["country"],
                              "station": g["station"], "period": g["period"],
                              "product": g["product_group"], "litres": round(qty, 1),
                              "issue": issue, "expected": round(expected, 4),
                              "actual": round(actual, 4), "recover_eur": rec,
                              "note": r["note"] or ""})
                total += rec
                by_sup[g["supplier"]] = round(by_sup.get(g["supplier"], 0.0) + rec, 2)
            exp = r["expected_discount_eur_l"]
            if exp is not None and applied < exp - tolerance:
                _flag("short discount", exp, applied, (exp - applied) * qty)
            mx = r["max_net_eur_l"]
            if mx is not None and eff_l > mx + tolerance:
                _flag("over ceiling", mx, eff_l, (eff_l - mx) * qty)
    flags.sort(key=lambda f: f["recover_eur"], reverse=True)
    return flags, {"total_recover": round(total, 2), "flags": len(flags),
                   "by_supplier": by_sup, "rules": len(rules)}


if __name__ == "__main__":
    import sys
    per = sys.argv[1] if len(sys.argv) > 1 else None
    fl, summ = audit(per)
    print(f"rules={summ['rules']}  flags={summ['flags']}  recoverable EUR {summ['total_recover']:,.2f}")
    for f in fl[:20]:
        print(f"  {f['supplier']:7} {f['country']:10} {f['period']} {f['issue']:14} "
              f"exp={f['expected']} act={f['actual']} -> EUR {f['recover_eur']:,.2f}  {f['note']}")
