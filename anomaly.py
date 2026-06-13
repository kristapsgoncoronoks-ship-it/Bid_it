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
  vehicle_price   a vehicle's NET EUR/L is a high outlier vs the FLEET's per-vehicle
                  spread this month (systematically expensive fuel — station choice /
                  card misuse / wrong supplier)
  off_period      a transaction dated outside the loaded period
  off_hours       diesel fuelled in the deep-night window (possible card misuse)
"""
import os, sys, sqlite3, collections, statistics

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/fuel_history.db"
# Thresholds are LEARNED from the data, never fixed: each check measures the relevant
# distribution's own spread (std-dev) and flags points beyond mean ± K·σ. ANOMALY_SIGMAS
# is the only knob — the statistical sensitivity (default 2.0 = the standard outlier
# distance), env-overridable — not a fuel-price number.
ANOMALY_SIGMAS = float(os.environ.get("ANOMALY_SIGMAS", "2.0"))
# Deep-night hours (local station time, 24h) at which a transport fleet fuelling is worth
# a human look — possible fuel-card misuse. Env-overridable as a comma-list of hours.
OFF_HOURS = tuple(int(h) for h in
                  os.environ.get("OFF_HOURS", "22,23,0,1,2,3,4").split(",") if h.strip())


def _has_col(con, table, col):
    """True if `table` has column `col`. The live schema always carries `time`; this guard
    keeps the off-hours/time-of-day analytics inert on a minimal table (e.g. older fixtures)
    rather than raising on a missing column."""
    try:
        return any(r[1] == col for r in con.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return False


def _parse_hour(t):
    """Hour-of-day (0-23) from a 'HH:MM' time string, or None for empty/malformed input.
    Defensive: never raises — bad/blank `time` rows just return None."""
    if not t:
        return None
    s = str(t).strip()
    parts = s.split(":")
    if not parts or not parts[0].isdigit():
        return None
    h = int(parts[0])
    return h if 0 <= h <= 23 else None

def _outlier_high(value, sample):
    """True if `value` is a high outlier of `sample` (beyond mean + K·σ). The bound is
    learned from the sample; returns False when there isn't enough spread/data."""
    if len(sample) < 3:
        return False
    sd = statistics.pstdev(sample)
    return sd > 0 and value > statistics.fmean(sample) + ANOMALY_SIGMAS * sd

def _robust_stats(sample):
    """Precompute the bucket-level stats (median, MAD, pstdev) that `_robust_outlier`
    needs, ONCE per sample — they're identical for every value tested against the same
    bucket. Returns None when the sample is too small to learn a bound (mirrors the
    `len(sample) < 3` guard in the per-value check)."""
    if len(sample) < 3:
        return None
    med = statistics.median(sample)
    mad = statistics.median([abs(x - med) for x in sample])
    sd = statistics.pstdev(sample)               # only used in the MAD==0 fallback branch
    return (med, mad, sd)

def _robust_flag(value, stats):
    """Iglewicz-Hoaglin modified z-score (median + MAD) — robust, NOT masked by the very
    point being tested (important for per-line checks in small per-period buckets). All
    learned from the sample (via precomputed `stats` from `_robust_stats`). Returns
    'HIGH' / 'LOW' / None. Byte-identical to the inlined median+MAD computation."""
    if stats is None:
        return None
    med, mad, sd = stats
    if mad > 0:
        z = 0.6745 * (value - med) / mad
        thr = 3.5                                # the standard modified-z cutoff
    else:                                        # most values identical -> use std-dev
        if sd <= 0:
            return None
        z = (value - med) / sd
        thr = ANOMALY_SIGMAS
    return "HIGH" if z > thr else "LOW" if z < -thr else None

def _robust_outlier(value, sample):
    """Convenience wrapper: compute the sample stats and flag `value` in one call.
    Kept for callers/tests that pass a raw sample; the hot path in `annotate` precomputes
    the stats once per bucket and calls `_robust_flag` directly."""
    return _robust_flag(value, _robust_stats(sample))


def expected_rebates(con):
    """Learn the typical effective rebate per litre per (supplier, country) from ALL
    HISTORY — the gap between the document price and the effective price (e.g. Q8's Port
    One rebate, which arrives on a SEPARATE invoice we often don't see). This lets us
    recognise/estimate discounting even where the rebate line isn't on the invoice."""
    out = {}
    for r in con.execute("""SELECT supplier, country,
            SUM(net_eur-net_eur_eff) reb, SUM(qty) q
            FROM transactions WHERE product_group='Diesel' AND (net_eur-net_eur_eff)>0.001
            GROUP BY supplier, country"""):
        if r["q"]:
            # full precision — never round currency in the analytics; format at display
            out[(r["supplier"], r["country"])] = r["reb"] / r["q"]
    return out


# product groups that are discount / adjustment lines rather than a fuel purchase
DISCOUNT_GROUPS = ("Promo adj", "Discount", "Rebate", "Credit")

def annotate(rows, hist_rebates=None):
    """Annotate transaction rows IN PLACE (same order, same physical position) so an
    anomaly or a discount can be read against the exact line it belongs to. `rows` are
    dicts with: country, period, supplier, product_group, qty, eurl (effective EUR/L),
    net_eur, net_eur_eff. Returns a list of annotation dicts aligned 1:1 with `rows`:
        anomaly          reason string if the line's price is a learned outlier, else None
        rebate           the discount actually applied on this line (net - effective)
        is_discount      True for a separate discount/adjustment line (e.g. Promo adj)
        relates_to       for a discount line: the supplier/country/period it applies to
        expected_rebate  estimated rebate (EUR) history says should apply but is missing
    Outliers are LEARNED per (country, period) from the rows — never a fixed threshold."""
    hist_rebates = hist_rebates or {}
    buckets = collections.defaultdict(list)
    for r in rows:
        if r.get("product_group") == "Diesel" and (r.get("qty") or 0) > 0 and r.get("eurl"):
            buckets[(r.get("country"), r.get("period"))].append(r["eurl"])
    # median + MAD are identical for every row in a bucket — compute them ONCE here
    # instead of recomputing per row inside _robust_outlier (and re-medianing at display).
    bucket_stats = {k: _robust_stats(s) for k, s in buckets.items()}
    out = []
    for r in rows:
        net = r.get("net_eur") or 0
        eff = r.get("net_eur_eff")
        rebate = (net - eff) if eff is not None else 0.0   # full precision; display rounds
        pg = r.get("product_group")
        is_discount = (net < 0) or (pg in DISCOUNT_GROUPS)
        anomaly = relates_to = expected_rebate = None
        if is_discount:
            relates_to = f"{r.get('supplier','')} {r.get('country','')} {r.get('period') or ''}".strip()
        stats = bucket_stats.get((r.get("country"), r.get("period")))
        if pg == "Diesel" and (r.get("qty") or 0) > 0 and r.get("eurl"):
            flag = _robust_flag(r["eurl"], stats)
            if flag:
                med = stats[0]                     # bucket median (== statistics.median(sample))
                tag = ((r.get("country") or "") + " " + (r.get("period") or "")).strip()
                anomaly = f"{r['eurl']:.3f} EUR/L — {flag} outlier vs {tag} median {med:.3f}"
        exp = hist_rebates.get((r.get("supplier"), r.get("country")))
        if exp and abs(rebate) < 0.005 and pg == "Diesel" and (r.get("qty") or 0) > 0:
            expected_rebate = exp * r["qty"]               # full precision; display rounds
        out.append({"anomaly": anomaly, "rebate": rebate, "is_discount": is_discount,
                    "relates_to": relates_to, "expected_rebate": expected_rebate})
    return out


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

    # vehicle price outlier — LEARN the FLEET's per-vehicle NET EUR/L distribution this
    # month and flag a vehicle whose volume-weighted price is a high outlier (beyond the
    # fleet mean + K·σ), not a fixed %. A systematically-expensive vehicle points at
    # station choice / card misuse / the wrong supplier. (True L/100km consumption would
    # need an odometer/distance field, which transactions does not carry — future work.)
    veh_prices = []   # [(vehicle, price)] for vehicles past the min-litres floor
    for r in con.execute(
        """SELECT vehicle, SUM(qty) q, SUM(net_eur_eff)/NULLIF(SUM(qty),0) p
           FROM transactions WHERE period=? AND product_group='Diesel'
           GROUP BY vehicle HAVING q>=100""", (period,)):   # 100 L floor: a per-vehicle
        if r["p"] is not None:                               # purchase, smaller than the
            veh_prices.append((r["vehicle"], r["p"]))        # 200 L station floor above
    if veh_prices:
        prices = [p for _, p in veh_prices]
        mean = statistics.fmean(prices)
        for veh, p in veh_prices:
            if _outlier_high(p, prices):
                flags.append(("vehicle_price", "warn",
                    f"vehicle {veh}: {p:.3f} EUR/L is a high outlier vs the fleet's "
                    f"{mean:.3f} mean this period"))

    # off-period dates (one flag per supplier+vehicle+date)
    for r in con.execute("""SELECT supplier, vehicle, date, COUNT(*) n FROM transactions
                            WHERE period=? AND substr(date,1,7)!=?
                            GROUP BY supplier, vehicle, date""", (period, period)):
        flags.append(("off_period", "warn",
            f"{r['supplier']} {r['vehicle']}: {r['n']} txn(s) dated {r['date']} (loaded under {period})"))

    # off-hours diesel fuelling (one flag per supplier+vehicle+date cluster). Parse the
    # hour from `time` in PYTHON (defensive: empty/malformed `time` is skipped, never
    # raises) and cluster the deep-night fuelings by (supplier, vehicle, date).
    night = collections.defaultdict(list)   # (supplier, vehicle, date) -> [time, ...]
    if _has_col(con, "transactions", "time"):
        for r in con.execute("""SELECT supplier, vehicle, date, time FROM transactions
                                WHERE period=? AND product_group='Diesel'
                                ORDER BY vehicle, date, time""", (period,)):
            h = _parse_hour(r["time"])
            if h is not None and h in OFF_HOURS:
                night[(r["supplier"], r["vehicle"], r["date"])].append(r["time"])
    for (supplier, vehicle, date), times in night.items():
        n = len(times)
        sample = times[0]
        flags.append(("off_hours", "warn",
            f"{supplier} {vehicle}: {n} off-hours diesel fuelling(s) on {date} "
            f"(e.g. {sample})"))

    con.close()
    return flags


def time_of_day_summary(period, con=None):
    """Hour-of-day distribution of DIESEL fuelings for `period` — the under-used
    `transactions.time` dimension surfaced as analytics. Per bucket: count of fuelings,
    total litres, and the VOLUME-WEIGHTED NET avg EUR/L (VAT-excluded). Rows with an
    empty/malformed `time` are not dropped — they roll up into an 'unknown' bucket so the
    totals stay honest. Pure/read-only; never raises. Returns a list of dicts ordered
    0..23 then 'unknown', each: {hour, count, litres, eur_l}."""
    own = con is None
    if own:
        con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    try:
        # accumulate in Python so we can hour-parse the same way the flag does (one source
        # of truth) and route bad `time` to the 'unknown' bucket.
        agg = {}   # hour-or-'unknown' -> [count, litres, net_eur_eff_sum]
        if not _has_col(con, "transactions", "time"):
            return []
        for r in con.execute("""SELECT time, qty, net_eur_eff FROM transactions
                                WHERE period=? AND product_group='Diesel'""", (period,)):
            h = _parse_hour(r["time"])
            key = h if h is not None else "unknown"
            a = agg.setdefault(key, [0, 0.0, 0.0])
            a[0] += 1
            a[1] += r["qty"] or 0
            a[2] += r["net_eur_eff"] or 0
        out = []
        order = list(range(24)) + ["unknown"]
        for key in order:
            if key not in agg:
                continue
            count, litres, net = agg[key]
            # full precision; format at display (NET EUR/L, VAT-excluded)
            eur_l = net / litres if litres else None
            label = f"{key:02d}:00" if isinstance(key, int) else "unknown"
            out.append({"hour": label, "count": count, "litres": litres, "eur_l": eur_l})
        return out
    finally:
        if own:
            con.close()


def vehicle_cost_summary(period, con=None):
    """Per-vehicle DIESEL fuel cost for `period` — the under-used `transactions.vehicle`
    dimension surfaced as analytics (which vehicles buy the most expensive fuel). Per
    vehicle: number of fuellings, total litres, total spend (SUM net_eur_eff) and the
    VOLUME-WEIGHTED NET EUR/L (VAT-excluded). Sorted by EUR/L desc so the costliest
    vehicles surface first. Pure/read-only; never raises. Accepts an optional `con`
    (opens/closes its own when None). Returns a list of dicts, each:
        {vehicle, litres, eur_l, spend, n_fuellings}."""
    own = con is None
    if own:
        con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    try:
        out = []
        for r in con.execute(
            """SELECT vehicle, COUNT(*) n, SUM(qty) q, SUM(net_eur_eff) spend
               FROM transactions WHERE period=? AND product_group='Diesel'
               GROUP BY vehicle""", (period,)):
            litres = r["q"] or 0
            spend = r["spend"] or 0.0
            # full precision; format at display (NET EUR/L, VAT-excluded)
            eur_l = spend / litres if litres else None
            out.append({"vehicle": r["vehicle"], "litres": litres, "eur_l": eur_l,
                        "spend": spend, "n_fuellings": r["n"]})
        # costliest first; None price (no litres) sinks to the bottom
        out.sort(key=lambda d: (d["eur_l"] is not None, d["eur_l"] or 0), reverse=True)
        return out
    finally:
        if own:
            con.close()


if __name__ == "__main__":
    period = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
    flags = find(period)
    if not flags:
        print(f"No anomalies for {period}.")
    for kind, level, msg in flags:
        print(f"  [{level}] {kind:14} {msg}")
    print(f"\n{len(flags)} flag(s).")
