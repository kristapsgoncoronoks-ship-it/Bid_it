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
    captured_at TEXT DEFAULT (datetime('now')),
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
        implied = r["nl"] / r["ne"]                      # full precision
        ecb_rate, ecb_date = ecb_rates.rate_for(r["currency"], r["last_date"])
        dev = (implied - ecb_rate) / ecb_rate * 100 if ecb_rate else None
        eur_diff = (r["ne"] - r["nl"] / ecb_rate) if ecb_rate else None
        con.execute("""INSERT INTO supplier_fx_history
            (supplier, currency, period, implied_rate, ecb_rate, ecb_date,
             deviation_pct, net_eur, eur_diff, captured_at)
            VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))
            ON CONFLICT(supplier, currency, period) DO UPDATE SET
              implied_rate=excluded.implied_rate, ecb_rate=excluded.ecb_rate,
              ecb_date=excluded.ecb_date, deviation_pct=excluded.deviation_pct,
              net_eur=excluded.net_eur, eur_diff=excluded.eur_diff,
              captured_at=datetime('now')""",
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
