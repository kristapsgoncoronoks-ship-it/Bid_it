"""
COMPETITOR FX HISTORY & CONTROL — track each supplier's implied exchange rate over time
and control it AGAINST THE MARKET (ECB).

For every (supplier, currency, period) we record the rate the supplier's invoices
effectively applied (net_local / net_eur), the official ECB reference rate for the
period (as-of the last fuelling date), and the deviation (markup) vs the market. The
series is stored as a historic pattern so we can see whether a supplier's FX markup is
INCREASING or DECREASING against the market period over period — and flag it when it
creeps up.

Stored in fuel_history.db (analytics, derived from transactions + ecb_rates); snapshot()
is idempotent so it can run on the FX page load and on each monthly close.
"""
import os, sqlite3, collections

import db_tuning
import ecb_rates

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/fuel_history.db"
_READY = set()
EPS = 0.1   # pp change below which a markup move is treated as 'stable' (noise floor)

SCHEMA = """
CREATE TABLE IF NOT EXISTS supplier_fx_history (
    supplier TEXT, currency TEXT, period TEXT,
    implied_rate REAL, ecb_rate REAL, ecb_date TEXT,
    deviation_pct REAL, net_eur REAL, eur_diff REAL,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (supplier, currency, period));
"""


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)
    if DB == ":memory:" or DB not in _READY:
        con.executescript(SCHEMA)
        if DB != ":memory:":
            _READY.add(DB)
    return con


def implied_vs_ecb(net_local, net_eur, currency, last_date):
    """CANONICAL FX math for one (supplier, currency) aggregate. Given the summed
    net_local / net_eur, the currency and the as-of (last fuelling) date, return
    (implied_rate, ecb_rate, ecb_date, deviation_pct, eur_diff).

    Convention (matches ecb_rates / the invoice data): rates are FOREIGN UNITS PER
    1 EUR, so the supplier's implied rate is net_local / net_eur and a EUR figure is
    net_local / rate. deviation_pct is the supplier's FX markup over the ECB market
    (positive = the invoice converted at a weaker EUR than ECB, i.e. cost the fleet
    more); eur_diff is the resulting EUR over/under-charge (net_eur minus what the ECB
    rate would have produced). All full precision — callers format at display.
    Returns (None,...) deviation/eur_diff when no ECB rate is available."""
    implied = net_local / net_eur                        # full precision
    ecb_rate, ecb_date = ecb_rates.rate_for(currency, last_date)
    dev = (implied - ecb_rate) / ecb_rate * 100 if ecb_rate else None
    eur_diff = (net_eur - net_local / ecb_rate) if ecb_rate else None
    return implied, ecb_rate, ecb_date, dev, eur_diff


def verify_invoices_fx(period=None, tolerance=0.02):
    """INDEPENDENT per-invoice ECB verification (owner-directed compliance): for each
    invoice/line segment compare the APPLIED rate (the stored fx_rate = net_local/net_eur)
    against the OFFICIAL ECB reference frozen on that line (fx_ecb_rate, fx_ecb_date,
    fx_source — written per line at consolidation by history.ecb_reference). Reads the
    STORED reference columns (no per-render rate_for); falls back to a live lookup ONLY for
    a line missing a stored reference (e.g. loaded before this column existed).

    Granularity is per (supplier, currency, period, date) — invoice/fuelling-day level, NOT
    the period aggregate the /fx page already shows. EUR lines (rate 1.0) are trivially OK
    and excluded. Returns a list of dicts sorted worst-deviation first:
      supplier, currency, period, date, lines, net_local, net_eur, applied_rate, ecb_rate,
      ecb_date, fx_source, deviation_pct, eur_diff, flagged (|dev| >= tolerance), no_ref
      (True when there is NO ECB reference to verify against — reported, never a false pass).

    `tolerance` is a FRACTION (0.02 = the established 2% threshold); deviation_pct is in
    percent. The deviation/eur_diff math mirrors implied_vs_ecb()'s convention (computed
    inline here because this path reads the STORED per-line ECB reference rather than
    looking it up per row)."""
    con = connect()
    has_cols = {r["name"] for r in con.execute("PRAGMA table_info(transactions)")}
    if "fx_ecb_rate" not in has_cols:
        con.close()
        return []
    where = "currency<>'EUR' AND currency IS NOT NULL"
    args = []
    if period:
        where += " AND period=?"; args.append(period)
    rows = con.execute(f"""SELECT supplier, currency, period, date,
            COUNT(*) lines, SUM(net_local) net_local, SUM(net_eur) net_eur,
            MAX(fx_ecb_rate) fx_ecb_rate, MAX(fx_ecb_date) fx_ecb_date,
            MAX(fx_source) fx_source
            FROM transactions WHERE {where}
            GROUP BY supplier, currency, period, date""", args).fetchall()
    con.close()
    out = []
    for r in rows:
        nl, ne = r["net_local"], r["net_eur"]
        if not ne:
            continue
        applied = nl / ne                              # full precision, = the stored fx_rate
        ecb_rate = r["fx_ecb_rate"]
        ecb_date = r["fx_ecb_date"]
        src = r["fx_source"]
        # Fall back to a live lookup for a line that predates the stored reference column.
        if ecb_rate is None and src is None:
            ecb_rate, ecb_date = ecb_rates.rate_for(r["currency"], r["date"])
            src = "ecb" if ecb_rate else "none"
        if ecb_rate:
            dev = (applied - ecb_rate) / ecb_rate * 100
            eur_diff = ne - nl / ecb_rate
            flagged = abs(dev) >= tolerance * 100
            no_ref = False
        else:
            dev = eur_diff = None
            flagged = False
            no_ref = True                              # no official reference -> NOT a pass
        out.append({"supplier": r["supplier"], "currency": r["currency"],
                    "period": r["period"], "date": r["date"], "lines": r["lines"],
                    "net_local": nl, "net_eur": ne, "applied_rate": applied,
                    "ecb_rate": ecb_rate, "ecb_date": ecb_date, "fx_source": src,
                    "deviation_pct": dev, "eur_diff": eur_diff,
                    "flagged": flagged, "no_ref": no_ref})
    out.sort(key=lambda x: -abs(x["deviation_pct"]) if x["deviation_pct"] is not None else 1)
    return out


def analysis_from_rows(rows, fields=None):
    """Per (supplier, currency) FX analysis straight from canonical ROW tuples (the
    consolidate pickle / build_master), with NO DB dependency — so the master workbook
    can surface the same FX markup the historic snapshot stores, without history.load
    having run first. EUR-only lines (currency EUR / blank) are skipped.

    `fields` is the canonical field-name order (defaults to the standard one); each row
    is a sequence positioned by it. Returns a list of dicts sorted by (supplier, currency):
    supplier, currency, litres, net_local, net_eur, implied_rate, ecb_rate, ecb_date,
    deviation_pct, eur_diff. Reuses implied_vs_ecb() — the math lives in ONE place."""
    fields = fields or ["entity", "supplier", "country", "vehicle", "date", "time",
                        "station", "product", "product_group", "qty", "currency",
                        "net_local", "vat_local", "gross_local", "net_eur", "vat_eur",
                        "net_eur_eff", "note"]
    ix = {name: i for i, name in enumerate(fields)}
    agg = collections.defaultdict(lambda: [0.0, 0.0, 0.0, ""])  # litres, nl, ne, last_date
    for r in rows:
        ccy = r[ix["currency"]]
        if not ccy or ccy == "EUR":
            continue
        k = (r[ix["supplier"]], ccy)
        a = agg[k]
        a[0] += r[ix["qty"]] or 0.0
        a[1] += r[ix["net_local"]] or 0.0
        a[2] += r[ix["net_eur"]] or 0.0
        d = r[ix["date"]]
        if d and d > a[3]:
            a[3] = d
    out = []
    for (sup, ccy), (litres, nl, ne, last_date) in sorted(agg.items()):
        if not ne:
            continue
        implied, ecb_rate, ecb_date, dev, eur_diff = implied_vs_ecb(nl, ne, ccy, last_date)
        out.append({"supplier": sup, "currency": ccy, "litres": litres,
                    "net_local": nl, "net_eur": ne, "implied_rate": implied,
                    "ecb_rate": ecb_rate, "ecb_date": ecb_date,
                    "deviation_pct": dev, "eur_diff": eur_diff})
    return out


def snapshot(period=None):
    """Compute and UPSERT each (supplier, currency, period) implied FX rate vs ECB into
    the historic store. Idempotent. Returns the number of rows captured."""
    con = connect()
    where, args = "currency<>'EUR' AND currency IS NOT NULL", []
    if period:
        where += " AND period=?"; args.append(period)
    rows = con.execute(f"""SELECT supplier, currency, period,
            SUM(net_local) nl, SUM(net_eur) ne, MAX(date) last_date
            FROM transactions WHERE {where}
            GROUP BY supplier, currency, period""", args).fetchall()
    n = 0
    for r in rows:
        if not r["ne"]:
            continue
        implied, ecb_rate, ecb_date, dev, eur_diff = implied_vs_ecb(
            r["nl"], r["ne"], r["currency"], r["last_date"])
        con.execute("""INSERT INTO supplier_fx_history
            (supplier, currency, period, implied_rate, ecb_rate, ecb_date,
             deviation_pct, net_eur, eur_diff, captured_at)
            VALUES (?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
            ON CONFLICT(supplier, currency, period) DO UPDATE SET
              implied_rate=excluded.implied_rate, ecb_rate=excluded.ecb_rate,
              ecb_date=excluded.ecb_date, deviation_pct=excluded.deviation_pct,
              net_eur=excluded.net_eur, eur_diff=excluded.eur_diff,
              captured_at=CURRENT_TIMESTAMP""",
            (r["supplier"], r["currency"], r["period"], implied, ecb_rate, ecb_date,
             dev, r["ne"], eur_diff))
        n += 1
    con.commit(); con.close()
    return n


def history(supplier=None, currency=None, limit=1000):
    con = connect()
    w, p = ["1=1"], []
    if supplier: w.append("supplier=?"); p.append(supplier)
    if currency: w.append("currency=?"); p.append(currency)
    rows = [dict(r) for r in con.execute(
        f"SELECT * FROM supplier_fx_history WHERE {' AND '.join(w)} "
        f"ORDER BY supplier, currency, period DESC LIMIT ?", p + [limit])]
    con.close()
    return rows


def trend():
    """CONTROL: per (supplier, currency), the latest markup vs the market and how it
    moved vs the previous period — direction (increasing / decreasing / stable) and the
    change in percentage points. Sorted with the biggest INCREASES (worsening) first.
    Increasing markup against the market is flagged."""
    con = connect()
    rows = con.execute("""SELECT supplier, currency, period, deviation_pct
        FROM supplier_fx_history WHERE deviation_pct IS NOT NULL
        ORDER BY supplier, currency, period""").fetchall()
    con.close()
    series = collections.defaultdict(list)
    for r in rows:
        series[(r["supplier"], r["currency"])].append((r["period"], r["deviation_pct"]))
    out = []
    for (sup, ccy), ser in series.items():
        lp, ld = ser[-1]
        if len(ser) < 2:
            out.append({"supplier": sup, "currency": ccy, "period": lp, "markup": ld,
                        "prev_period": None, "delta": None, "direction": "new",
                        "increasing": False, "points": len(ser)})
            continue
        pp, pd = ser[-2]
        delta = ld - pd
        direction = ("increasing" if delta > EPS else "decreasing" if delta < -EPS else "stable")
        out.append({"supplier": sup, "currency": ccy, "period": lp, "markup": ld,
                    "prev_period": pp, "prev_markup": pd, "delta": delta,
                    "direction": direction, "increasing": delta > EPS, "points": len(ser)})
    out.sort(key=lambda x: -(x["delta"] if x["delta"] is not None else -999))
    return out


if __name__ == "__main__":
    print(f"captured {snapshot()} (supplier, currency, period) FX points")
    for t in trend()[:20]:
        d = "" if t["delta"] is None else f"{t['delta']:+.2f}pp"
        print(f"  {t['supplier']:7} {t['currency']} {t['period']}: markup {t['markup']:+.2f}% "
              f"{t['direction']:10} {d}")
