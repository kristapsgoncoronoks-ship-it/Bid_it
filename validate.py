"""
VALIDATION LAYER - cross-examine extracted/entered statement lines before commit.
Never trusts a single source: each line must survive independent checks, and the
batch must tie to the coversheet. Output drives the green/amber/red review screen.

Checks per line:
  rate      vat/net matches a known VAT rate for the country (±0.5pp)
  sign      net>0, vat>=0, vat<=net (no negative/over-100% VAT)
  range     amounts within sane bounds (not 0, not absurd)
  fields    invoice_no present, date parseable, country recognised
Batch checks:
  tie       sum(net+vat) equals the stated coversheet/statement total (±0.02)
  regress   if this batch was confirmed before, values must reproduce (no silent drift)

verdict per line: ok | warn | error ; a line with any error blocks commit until fixed.
"""
import os, re, sqlite3, datetime, json
from datetime import timezone as _tz

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/fuel_history.db"

# Standard VAT rates by country (full + reduced where fuel-relevant). Used as a
# coherence check, not a hard rule - reduced rates (e.g. PL diesel 8%) are allowed.
VAT_RATES = {
    "Austria": [20], "Belgium": [21], "Bulgaria": [20], "Croatia": [25],
    "Czechia": [21], "Denmark": [25], "Estonia": [22, 20], "Finland": [24, 25.5],
    "France": [20], "Germany": [19], "Hungary": [27], "Italy": [22],
    "Latvia": [21], "Lithuania": [21], "Luxembourg": [17], "Netherlands": [21],
    "Poland": [23, 8], "Portugal": [23], "Romania": [19], "Slovakia": [20],
    "Slovenia": [22], "Spain": [21, 10], "Sweden": [25],
}
COUNTRIES = set(VAT_RATES)


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def check_line(ln):
    """-> (verdict, [messages]). ln: dict invoice_no,date,country,currency,net,vat."""
    msgs, level = [], "ok"
    def bump(l):
        nonlocal level
        order = {"ok": 0, "warn": 1, "error": 2}
        if order[l] > order[level]: level = l
    net, vat = _f(ln.get("net")), _f(ln.get("vat"))
    ctry = (ln.get("country") or "").strip()
    # fields
    if not (ln.get("invoice_no") or "").strip():
        msgs.append("invoice number missing"); bump("error")
    d = (ln.get("date") or "").strip()
    if d and not re.match(r"\d{4}-\d{2}-\d{2}$", d):
        msgs.append(f"date '{d}' not YYYY-MM-DD"); bump("warn")
    if ctry and ctry not in COUNTRIES:
        msgs.append(f"country '{ctry}' unrecognised"); bump("warn")
    # signs / ranges
    if net is None or vat is None:
        msgs.append("net or vat not a number"); bump("error"); return level, msgs
    if net <= 0:
        msgs.append("net must be > 0"); bump("error")
    if vat < 0:
        msgs.append("vat is negative"); bump("error")
    if net > 0 and vat > net:
        msgs.append("vat exceeds net (>100%)"); bump("error")
    if net > 5_000_000:
        msgs.append("net implausibly large"); bump("warn")
    # rate coherence
    if net and net > 0 and vat is not None and ctry in VAT_RATES:
        rate = vat / net * 100
        if vat == 0:
            pass  # zero-VAT lines validated elsewhere (domestic/exempt)
        elif not any(abs(rate - r) <= 0.5 for r in VAT_RATES[ctry]):
            exp = "/".join(str(r) for r in VAT_RATES[ctry])
            msgs.append(f"VAT {rate:.1f}% not a {ctry} rate ({exp}%)"); bump("warn")
    return level, msgs


def validate_batch(lines, coversheet_total=None):
    """lines: list of dicts. Returns per-line results + batch summary."""
    results = []
    for ln in lines:
        v, m = check_line(ln)
        results.append({"line": ln, "verdict": v, "messages": m})
    gross = round(sum((_f(l.get("net")) or 0) + (_f(l.get("vat")) or 0) for l in lines), 2)
    tie = None
    if coversheet_total is not None:
        diff = round(gross - float(coversheet_total), 2)
        tie = {"gross": gross, "stated": round(float(coversheet_total), 2),
               "diff": diff, "ok": abs(diff) <= 0.02}
    errors = sum(1 for r in results if r["verdict"] == "error")
    warns = sum(1 for r in results if r["verdict"] == "warn")
    can_commit = errors == 0 and (tie is None or tie["ok"])
    return {"lines": results, "gross": gross, "tie": tie,
            "errors": errors, "warnings": warns, "can_commit": can_commit}


# ---------------------------------------------------------------- regression store
def _con():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS extraction_baseline (
        supplier TEXT, statement_ref TEXT, invoice_no TEXT,
        net REAL, vat REAL, confirmed_at TEXT,
        PRIMARY KEY (supplier, statement_ref, invoice_no))""")
    return con

def save_baseline(supplier, statement_ref, lines):
    """Record confirmed figures as the known-good answer for this batch."""
    con = _con()
    for ln in lines:
        con.execute("""INSERT OR REPLACE INTO extraction_baseline
            VALUES (?,?,?,?,?,?)""", (supplier, statement_ref, ln.get("invoice_no"),
            _f(ln.get("net")) or 0, _f(ln.get("vat")) or 0,
            datetime.datetime.now(_tz.utc).isoformat(timespec="seconds")))
    con.commit(); con.close()

def regression_check(supplier, statement_ref, lines):
    """Compare current extraction against a previously confirmed baseline.
    -> list of drifts (empty if none or no baseline)."""
    con = _con()
    base = {r["invoice_no"]: (r["net"], r["vat"]) for r in con.execute(
        "SELECT * FROM extraction_baseline WHERE supplier=? AND statement_ref=?",
        (supplier, statement_ref))}
    con.close()
    drifts = []
    for ln in lines:
        b = base.get(ln.get("invoice_no"))
        if b:
            n, v = _f(ln.get("net")) or 0, _f(ln.get("vat")) or 0
            if abs(n - b[0]) > 0.02 or abs(v - b[1]) > 0.02:
                drifts.append(f"{ln.get('invoice_no')}: now {n:.2f}/{v:.2f}, "
                              f"baseline {b[0]:.2f}/{b[1]:.2f}")
    return drifts


if __name__ == "__main__":
    demo = [
        {"invoice_no": "BE001", "date": "2026-05-31", "country": "Belgium", "net": 27464.06, "vat": 5767.45},
        {"invoice_no": "DE001", "date": "2026-05-31", "country": "Germany", "net": 7382.95, "vat": 1402.76},
        {"invoice_no": "X1", "date": "2026-05-31", "country": "Belgium", "net": 1000, "vat": 190},   # wrong rate (19% in BE)
        {"invoice_no": "", "date": "bad", "country": "Narnia", "net": -5, "vat": 9999},               # multiple errors
    ]
    r = validate_batch(demo, coversheet_total=27464.06+5767.45+7382.95+1402.76+1190+9994)
    for x in r["lines"]:
        print(f"  [{x['verdict']:5}] {x['line'].get('invoice_no') or '(none)':8} {'; '.join(x['messages']) or 'clean'}")
    print("batch:", {k: r[k] for k in ("gross", "errors", "warnings", "can_commit")})
    print("tie:", r["tie"])
