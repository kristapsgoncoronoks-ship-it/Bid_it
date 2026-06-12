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
import os, sys, sqlite3, collections, statistics

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/fuel_history.db"
# Thresholds are LEARNED from the data, never fixed: each check measures the relevant
# distribution's own spread (std-dev) and flags points beyond mean ± K·σ. ANOMALY_SIGMAS
# is the only knob — the statistical sensitivity (default 2.0 = the standard outlier
# distance), env-overridable — not a fuel-price number.
ANOMALY_SIGMAS = float(os.environ.get("ANOMALY_SIGMAS", "2.0"))

def _outlier_high(value, sample):
    """True if `value` is a high outlier of `sample` (beyond mean + K·σ). The bound is
    learned from the sample; returns False when there isn't enough spread/data."""
    if len(sample) < 3:
        return False
    sd = statistics.pstdev(sample)
    return sd > 0 and value > statistics.fmean(sample) + ANOMALY_SIGMAS * sd


def find(period):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    flags = []

    # station price outlier — LEARN each country's price distribution this month and
    # flag stations that are statistical outliers (beyond mean + K·σ of that country's
    # own station prices), not a fixed % above average.
    stns = collections.defaultdict(list)   # country -> [(station, supplier, price)]
    for r in con.execute(
        """SELECT country, station, supplier, SUM(qty) q, SUM(net_eur_eff)/NULLIF(SUM(qty),0) p
           FROM transactions WHERE period=? AND product_group='Diesel'
           GROUP BY country, station HAVING q>=200""", (period,)):
        if r["p"] is not None:
            stns[r["country"]].append((r["station"], r["supplier"], r["p"]))
    for country, items in stns.items():
        prices = [p for _, _, p in items]
        mean = statistics.fmean(prices)
        for station, supplier, p in items:
            if _outlier_high(p, prices):
                flags.append(("station_price", "warn",
                    f"{supplier} {station} ({country}): {p:.3f} EUR/L is a high outlier "
                    f"vs {country}'s {mean:.3f} mean this period"))

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
        # LEARN how much suppliers' month-over-month moves vary, and flag a supplier only
        # when its move is a statistical outlier vs the market (beyond the median move by
        # more than K·σ of all the moves). A market-wide swing has near-zero spread of
        # divergences -> nothing flagged; only genuine divergence stands out.
        moves = {k: p / old[k] - 1 for k, p in cur.items() if k in old and old[k]}
        vals = list(moves.values())
        if len(vals) >= 3 and statistics.pstdev(vals) > 0:
            market = statistics.median(vals)
            sd = statistics.pstdev(vals)
            for k, move in moves.items():
                if abs(move - market) > ANOMALY_SIGMAS * sd:
                    flags.append(("price_divergence", "warn",
                        f"{k[0]} {k[1]}: {old[k]:.3f} -> {cur[k]:.3f} EUR/L ({move*100:+.0f}%) "
                        f"vs market {market*100:+.0f}% — diverges from the market move"))

    # vehicle volume spike vs own trailing average
    hist = collections.defaultdict(list)
    for r in con.execute("""SELECT vehicle, period, SUM(qty) q FROM transactions
                            WHERE product_group='Diesel' GROUP BY vehicle, period"""):
        hist[r["vehicle"]].append((r["period"], r["q"]))
    for veh, series in hist.items():
        cur = dict(series).get(period)
        past = [q for p, q in series if p != period]
        # LEARN the vehicle's OWN volume distribution and flag a spike beyond its own
        # mean + K·σ — not a fixed multiple of its average.
        if cur and _outlier_high(cur, past):
            avgp = statistics.fmean(past)
            flags.append(("volume_spike", "warn",
                f"vehicle {veh}: {cur:.0f} L this month vs its own {avgp:.0f} L average "
                f"(beyond its normal range)"))

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
