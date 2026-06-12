"""
ANOMALY DETECTION - flag transactions/stations worth a human look.
Read-only over fuel_history.db; no thresholds hardcoded into the data, all relative.

    python3 anomaly.py [period]

Flags (all RELATIVE — fuel prices swing widely over time, so we never use an absolute
price level or an absolute month-over-month limit):
  station_price   a station's diesel >X% above its country's average THAT MONTH
  price_divergence a supplier's price moved much more (or less) than the MARKET moved
                   that month — i.e. it diverged from everyone else, not just "moved"
  volume_spike    a vehicle's monthly litres far above its own trailing average
  off_period      a transaction dated outside the loaded period
"""
import os, sys, sqlite3, collections

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/fuel_history.db"
STATION_PCT = 0.15        # 15% above its country's average in the SAME month
DIVERGENCE_PCT = 0.10     # supplier's MoM move diverges 10pp from the market's MoM move
VOLUME_MULT = 2.5         # 2.5x own average


def find(period):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    flags = []

    # station price vs country average (diesel)
    avg = {r["country"]: r["a"] for r in con.execute(
        """SELECT country, SUM(net_eur_eff)/NULLIF(SUM(qty),0) a FROM transactions
           WHERE period=? AND product_group='Diesel' GROUP BY country""", (period,))}
    for r in con.execute(
        """SELECT country, station, supplier, SUM(qty) q, SUM(net_eur_eff)/NULLIF(SUM(qty),0) p
           FROM transactions WHERE period=? AND product_group='Diesel'
           GROUP BY country, station HAVING q>=200""", (period,)):
        a = avg.get(r["country"])
        if a and r["p"] > a * (1 + STATION_PCT):
            flags.append(("station_price", "warn",
                f"{r['supplier']} {r['station']} ({r['country']}): {r['p']:.3f} EUR/L "
                f"is {(r['p']/a-1)*100:.0f}% over {r['country']} avg {a:.3f}"))

    # month-over-month price jump per supplier+country
    periods = [p[0] for p in con.execute("SELECT DISTINCT period FROM transactions ORDER BY period")]
    if period in periods and periods.index(period) > 0:
        prev = periods[periods.index(period) - 1]
        def pc(per):
            return {(r["supplier"], r["country"]): r["p"] for r in con.execute(
                """SELECT supplier, country, SUM(net_eur_eff)/NULLIF(SUM(qty),0) p
                   FROM transactions WHERE period=? AND product_group='Diesel'
                   GROUP BY supplier, country""", (per,))}
        cur, old = pc(period), pc(prev)
        # The market itself moves week to week — flag a supplier only when its move
        # DIVERGES from the overall market move (median across suppliers), so normal
        # volatility (everyone moving together) is never flagged.
        moves = sorted(p/old[k]-1 for k, p in cur.items() if k in old and old[k])
        if moves:
            market = moves[len(moves)//2]            # median market MoM move
            for k, p in cur.items():
                if k in old and old[k]:
                    move = p/old[k]-1
                    div = move - market
                    if abs(div) > DIVERGENCE_PCT:
                        flags.append(("price_divergence", "warn",
                            f"{k[0]} {k[1]}: {old[k]:.3f} -> {p:.3f} EUR/L ({move*100:+.0f}%) "
                            f"vs market {market*100:+.0f}% — diverged {div*100:+.0f}pp"))

    # vehicle volume spike vs own trailing average
    hist = collections.defaultdict(list)
    for r in con.execute("""SELECT vehicle, period, SUM(qty) q FROM transactions
                            WHERE product_group='Diesel' GROUP BY vehicle, period"""):
        hist[r["vehicle"]].append((r["period"], r["q"]))
    for veh, series in hist.items():
        cur = dict(series).get(period)
        past = [q for p, q in series if p != period]
        if cur and past:
            avgp = sum(past)/len(past)
            if avgp > 0 and cur > avgp * VOLUME_MULT:
                flags.append(("volume_spike", "warn",
                    f"vehicle {veh}: {cur:.0f} L this month vs {avgp:.0f} L average ({cur/avgp:.1f}x)"))

    # off-period dates (one flag per supplier+vehicle+date)
    for r in con.execute("""SELECT supplier, vehicle, date, COUNT(*) n FROM transactions
                            WHERE period=? AND substr(date,1,7)!=?
                            GROUP BY supplier, vehicle, date""", (period, period)):
        flags.append(("off_period", "warn",
            f"{r['supplier']} {r['vehicle']}: {r['n']} txn(s) dated {r['date']} (loaded under {period})"))

    con.close()
    return flags


if __name__ == "__main__":
    period = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
    flags = find(period)
    if not flags:
        print(f"No anomalies for {period}.")
    for kind, level, msg in flags:
        print(f"  [{level}] {kind:14} {msg}")
    print(f"\n{len(flags)} flag(s).")
