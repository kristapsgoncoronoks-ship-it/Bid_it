"""excise.py — DIESEL EXCISE-DUTY (fuel-tax) refund ESTIMATE: the parallel claim engine to the
VAT refund, on the SAME validated line-item data.

~7 EU member states grant commercial road hauliers a PARTIAL refund of the excise duty on
diesel (commercial / "professional" diesel): Belgium, France, Italy, Slovenia, Hungary, Spain,
Croatia. The refund is a per-1000-litre amount that CHANGES (often yearly) and is conditional
(commercial diesel, vehicle ≥ 7.5 t, fuel paid by card, registered carrier). Every fleet-card
incumbent (DKV, Eurowag, UTA, FastVAT, Vialtis) sells this; we already hold the per-litre,
per-station, per-country diesel quantities — so this is a second recoverable-cash stream over
data we own.

ADVISORY by design: the per-country rate is an INDICATIVE default that an admin MUST verify /
override against the current statutory rate, and the figure does not assert eligibility (vehicle
weight / carrier registration are not modelled). Reads transactions READ-ONLY via
`dataproduct.connect("fuel_history")`; writes no product DB; never raises -> a safe empty shape.
"""
import applog
import money

log = applog.get("excise")

# Indicative DEFAULT refund rate (EUR per 1,000 L) per eligible country — a PLACEHOLDER in the
# €25–33/1,000 L band reported for FR/BE/IT commercial-diesel reclaim. These are NOT statutory
# figures: an admin sets the current rate per country (set_rate) before the estimate is relied
# on. country name matches the `transactions.country` values.
_DEFAULT_RATE_EUR_PER_1000L = 30.0
REFUND_COUNTRIES = {
    "Belgium": _DEFAULT_RATE_EUR_PER_1000L,
    "France": _DEFAULT_RATE_EUR_PER_1000L,
    "Italy": _DEFAULT_RATE_EUR_PER_1000L,
    "Slovenia": _DEFAULT_RATE_EUR_PER_1000L,
    "Hungary": _DEFAULT_RATE_EUR_PER_1000L,
    "Spain": _DEFAULT_RATE_EUR_PER_1000L,
    "Croatia": _DEFAULT_RATE_EUR_PER_1000L,
}
_SETTING_PREFIX = "excise_rate_"          # app_settings key per country (admin override)
RATES_ARE_INDICATIVE = True               # surfaced in the UI so the figure is never asserted


def is_refund_country(country):
    return (country or "").strip() in REFUND_COUNTRIES


def rate_for(country):
    """Effective refund rate (EUR per 1,000 L) for a country: the admin override if set, else
    the indicative default; None when the country grants no refund. Never raises."""
    c = (country or "").strip()
    if c not in REFUND_COUNTRIES:
        return None
    try:
        import auth
        v = auth.get_setting(_SETTING_PREFIX + c, "")
        if v not in (None, ""):
            return float(v)
    except Exception as e:
        log.warning("rate_for(%s) override read failed: %s", c, e)
    return REFUND_COUNTRIES[c]


def set_rate(country, eur_per_1000l, actor=None):
    """Admin override of a country's excise refund rate (EUR per 1,000 L). Returns (True,"") or
    (False, error). A blank/None value CLEARS the override (back to the indicative default)."""
    c = (country or "").strip()
    if c not in REFUND_COUNTRIES:
        return False, f"{c or '(blank)'} is not an excise-refund country"
    try:
        import auth
        if eur_per_1000l in (None, ""):
            auth.set_setting(_SETTING_PREFIX + c, "")
            return True, ""
        r = float(eur_per_1000l)
        if r < 0:
            return False, "the rate must be ≥ 0"
        auth.set_setting(_SETTING_PREFIX + c, str(r))
        return True, ""
    except (TypeError, ValueError):
        return False, "the rate must be a number (EUR per 1,000 L)"
    except Exception as e:
        log.warning("set_rate(%s) failed: %s", c, e)
        return False, f"could not save the rate ({str(e)[:60]})"


def rates():
    """The effective rate table {country: eur_per_1000l, ...} for the UI (override or default)."""
    return {c: rate_for(c) for c in sorted(REFUND_COUNTRIES)}


def excise_report(period=None):
    """Estimated recoverable diesel excise per (entity, country), from the validated diesel
    transactions in the eligible refund countries. `period` filters to one close period (e.g.
    '2026-05'); None = all periods. Returns {rows, summary} where
      rows    = [{entity, country, litres, rate_eur_per_1000l, recoverable_eur}] (desc by €)
      summary = {recoverable_eur, litres, countries, indicative}
    NET/quantity basis; the € figure is ADVISORY (rate + eligibility must be confirmed). Reads
    READ-ONLY via dataproduct; never raises -> a safe empty shape."""
    empty = {"rows": [], "summary": {"recoverable_eur": 0.0, "litres": 0.0,
                                     "countries": 0, "indicative": RATES_ARE_INDICATIVE}}
    try:
        import dataproduct
        con = dataproduct.connect("fuel_history")
        try:
            sql = ("SELECT entity, country, ROUND(SUM(qty),0) AS litres "
                   "FROM transactions WHERE product_group='Diesel' AND qty>0")
            params = []
            if period:
                sql += " AND period=?"
                params.append(period)
            sql += " GROUP BY entity, country"
            raw = con.execute(sql, params).fetchall()
        finally:
            con.close()
        rows = []
        for r in raw:
            ctry = r["country"]
            rate = rate_for(ctry)
            if not rate:                              # not an excise-refund country
                continue
            litres = float(r["litres"] or 0)
            rec = money.f2(litres * rate / 1000.0)
            rows.append({"entity": r["entity"], "country": ctry,
                         "litres": litres, "rate_eur_per_1000l": rate,
                         "recoverable_eur": rec})
        rows.sort(key=lambda x: x["recoverable_eur"], reverse=True)
        total = money.fsum(x["recoverable_eur"] for x in rows)
        litres = money.fsum(x["litres"] for x in rows)
        return {"rows": rows,
                "summary": {"recoverable_eur": float(total), "litres": float(litres),
                            "countries": len({x["country"] for x in rows}),
                            "indicative": RATES_ARE_INDICATIVE}}
    except Exception as e:
        log.warning("excise_report(%s) failed: %s", period, e)
        return empty


def recoverable_total(period=None):
    """Just the total estimated recoverable diesel excise (EUR) — for the recovery dashboard.
    Never raises -> 0.0."""
    try:
        return float(excise_report(period)["summary"]["recoverable_eur"])
    except Exception as e:
        log.warning("recoverable_total(%s) failed: %s", period, e)
        return 0.0
