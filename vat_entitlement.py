"""vat_entitlement.py — PER-COUNTRY VAT RECOVERABILITY (deductibility) + the fuel-card
entitlement rule. The defensible-moat layer: how much of the input VAT a refund country
actually lets you DEDUCT varies by country (DE ~100%, BE historically 50% for mixed-use, some
0% on certain categories), and a claim that over-states the recoverable VAT gets refused — so
the "claimable" figure must reflect each country's recoverability.

ADVISORY by design (like excise.py): the per-country recoverable % is an admin-set figure
that DEFAULTS to 100% (commercial road-transport diesel/toll generally recovers in full); the
known per-country caveats are surfaced as HINTS to verify, never asserted as law. Reads/writes
nothing in the product DBs; rates live in app_settings; never raises.

The FUEL-CARD-vs-END-USER rule (ECJ + German BMF Jan-2025): the END-USER (the fleet), not the
card issuer, is generally entitled to recover, and a national approval "does not apply abroad"
— so foreign VAT can become unrecoverable unless the invoice is to the claiming entity. Carried
here as ENTITLEMENT_NOTE for the readiness surface.
"""
import applog
import money

log = applog.get("vat_entitlement")

DEFAULT_RECOVERABLE_PCT = 100.0           # commercial road transport generally recovers in full
_SETTING_PREFIX = "vat_recover_pct_"      # app_settings key per country (admin override)

# Advisory caveats a human should VERIFY for the fleet's vehicle class — NOT applied as a rate.
RECOVERABILITY_HINTS = {
    "Belgium": "Belgium historically restricts fuel VAT (e.g. 50%) for mixed/private-use "
               "vehicles — for commercial trucks it is generally fully recoverable; verify.",
    "France": "France phased out the diesel-VAT recovery restriction for commercial vehicles "
              "(petrol differs) — verify the current rate and fuel type.",
    "Spain": "Spain: commercial-vehicle fuel generally recoverable; verify passenger/mixed use.",
    "Italy": "Italy applies use-based limitations on some vehicle fuel — verify the class.",
}

# The fuel-card-vs-end-user entitlement rule (advisory readiness note).
ENTITLEMENT_NOTE = (
    "Entitlement (fuel-card rule): the END USER — your fleet entity — must be the party "
    "entitled to recover, i.e. the invoice must name your entity as the customer (not the "
    "card issuer / a factoring entity), and a domestic approval does not extend abroad "
    "(ECJ; German BMF Jan 2025). Capture reads the legal entity off the invoice; confirm the "
    "customer on the claim is the entitled end user.")


def recoverable_pct(country):
    """The recoverable-VAT percentage for a refund country (admin override, else 100). A value
    in [0, 100]. Never raises -> 100."""
    c = (country or "").strip()
    if not c:
        return DEFAULT_RECOVERABLE_PCT
    try:
        import auth
        v = auth.get_setting(_SETTING_PREFIX + c, "")
        if v not in (None, ""):
            r = float(v)
            return max(0.0, min(100.0, r))
    except Exception as e:
        log.warning("recoverable_pct(%s) read failed: %s", c, e)
    return DEFAULT_RECOVERABLE_PCT


def set_recoverable_pct(country, pct, actor=None):
    """Admin override of a country's recoverable %. A blank/None CLEARS it (back to 100).
    Returns (True,"") or (False,error). Never raises."""
    c = (country or "").strip()
    if not c:
        return False, "a country is required"
    try:
        import auth
        if pct in (None, ""):
            auth.set_setting(_SETTING_PREFIX + c, "")
            return True, ""
        r = float(pct)
        if not (0.0 <= r <= 100.0):
            return False, "the percentage must be between 0 and 100"
        auth.set_setting(_SETTING_PREFIX + c, str(r))
        return True, ""
    except (TypeError, ValueError):
        return False, "the percentage must be a number (0–100)"
    except Exception as e:
        log.warning("set_recoverable_pct(%s) failed: %s", c, e)
        return False, f"could not save the rate ({str(e)[:60]})"


def recoverable_eur(country, vat_eur):
    """The recoverable portion of a claim's VAT after the country's deductibility. Never raises."""
    try:
        return float(money.f2((float(vat_eur or 0)) * recoverable_pct(country) / 100.0))
    except Exception as e:
        log.warning("recoverable_eur(%s) failed: %s", country, e)
        return float(vat_eur or 0)


def configured_countries():
    """Countries with a NON-default (<100%) recoverable rate set — the ones that haircut a
    claim. Returns {country: pct}. Used to flag the dashboard only when it matters."""
    out = {}
    try:
        import auth
        # app_settings has no prefix scan helper here; check the countries the app knows about.
        for c in _known_countries():
            p = recoverable_pct(c)
            if p < DEFAULT_RECOVERABLE_PCT:
                out[c] = p
    except Exception as e:
        log.warning("configured_countries failed: %s", e)
    return out


def _known_countries():
    """The refund/supply countries the system actually sees (from the transactions), so the
    config page lists real countries. READ-ONLY via dataproduct; never raises -> a sane list."""
    try:
        import dataproduct
        con = dataproduct.connect("fuel_history")
        try:
            rows = con.execute("SELECT DISTINCT country FROM transactions "
                               "WHERE country IS NOT NULL AND country<>'' ORDER BY country")
            cs = [r["country"] for r in rows]
        finally:
            con.close()
        return cs or sorted(RECOVERABILITY_HINTS)
    except Exception as e:
        log.warning("known_countries failed: %s", e)
        return sorted(RECOVERABILITY_HINTS)


def country_table():
    """[(country, pct, hint), ...] for the config page — every known refund country with its
    effective recoverable % and any advisory caveat."""
    return [(c, recoverable_pct(c), RECOVERABILITY_HINTS.get(c, ""))
            for c in _known_countries()]


def recoverable_summary(year):
    """Apply the per-country recoverability to the year's claimable VAT. Reads the CANONICAL
    vat_refund.claims_overview (whose to_submit + open rows carry country + vat_eur) and scales
    each by its country rate. Returns {gross_eur, recoverable_eur, haircut_eur, by_country:[{...}]}
    — the over-claim that per-country deductibility removes. Advisory; never raises."""
    empty = {"gross_eur": 0.0, "recoverable_eur": 0.0, "haircut_eur": 0.0, "by_country": []}
    try:
        import vat_refund as VR
        ov = VR.claims_overview(str(year))
        by = {}
        for c in (ov.get("to_submit", []) + ov.get("open", [])):
            ctry = c.get("country") or ""
            v = float(c.get("vat_eur") or 0)
            b = by.setdefault(ctry, {"country": ctry, "gross": 0.0, "recoverable": 0.0,
                                     "pct": recoverable_pct(ctry)})
            b["gross"] += v
            b["recoverable"] += recoverable_eur(ctry, v)
        for b in by.values():
            b["gross"] = float(money.f2(b["gross"]))
            b["recoverable"] = float(money.f2(b["recoverable"]))
            b["haircut"] = float(money.f2(b["gross"] - b["recoverable"]))
        gross = float(money.fsum(b["gross"] for b in by.values()))
        rec = float(money.fsum(b["recoverable"] for b in by.values()))
        return {"gross_eur": gross, "recoverable_eur": rec,
                "haircut_eur": float(money.f2(gross - rec)),
                "by_country": sorted(by.values(), key=lambda x: -x["gross"])}
    except Exception as e:
        log.warning("recoverable_summary(%s) failed: %s", year, e)
        return empty
