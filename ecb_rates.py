"""
ECB REFERENCE RATES - fetch the European Central Bank euro foreign-exchange
reference rates and cache them locally (ecb_rates.db) so the FX-comparison page
can check each invoice's effective exchange rate against the official ECB rate.

Rates are stored as "foreign units per 1 EUR" (the ECB convention: 1 EUR = rate),
which matches the invoice implied rate net_local / net_eur, so the two compare
directly.

Source (no API key needed):
  daily:    https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
  90 days:  https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml

The fetch is triggered on demand (a button in the web UI). If the ECB host is
unreachable (offline, or blocked by a network policy), fetch_and_store raises a
clean error the caller surfaces to the user; cached rates keep working.
"""
import os
import sqlite3
import xml.etree.ElementTree as ET

import requests

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/ecb_rates.db"

DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
HIST_90D_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS ecb_fx (
        date TEXT, currency TEXT, rate REAL,
        PRIMARY KEY (date, currency))""")
    return con


def _parse(xml_text):
    """ECB eurofxref XML -> list of (date, currency, rate). Namespace-agnostic."""
    root = ET.fromstring(xml_text)
    ln = lambda t: t.rsplit("}", 1)[-1]
    out = []
    for el in root.iter():
        if ln(el.tag) != "Cube" or "time" not in el.attrib:
            continue
        d = el.attrib["time"]
        for child in el:
            a = child.attrib
            if "currency" in a and "rate" in a:
                try:
                    out.append((d, a["currency"], float(a["rate"])))
                except ValueError:
                    pass
    return out


def fetch_and_store(hist=True, timeout=12):
    """Fetch ECB rates and upsert into the cache. Returns dict(asof, days, rows,
    currencies). Raises RuntimeError with a readable message on network/parse
    failure so the UI can show it."""
    url = HIST_90D_URL if hist else DAILY_URL
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"could not reach the ECB ({url}): {e}") from e
    rows = _parse(r.text)
    if not rows:
        raise RuntimeError("ECB response contained no rates (format changed?)")
    con = connect()
    con.executemany(
        "INSERT OR REPLACE INTO ecb_fx (date, currency, rate) VALUES (?,?,?)", rows)
    # EUR base rate (1.0) for completeness
    days = sorted({d for d, _, _ in rows})
    con.executemany("INSERT OR REPLACE INTO ecb_fx (date, currency, rate) VALUES (?,?,1.0)",
                    [(d, "EUR") for d in days])
    con.commit(); con.close()
    return {"asof": days[-1] if days else None, "days": len(days),
            "rows": len(rows), "currencies": sorted({c for _, c, _ in rows})}


def rate_for(currency, on_date=None):
    """ECB rate (foreign per 1 EUR) for a currency on/just before a date. With no
    date, the most recent cached rate. Returns (rate, asof_date) or (None, None)."""
    if currency == "EUR":
        return (1.0, on_date)
    if not os.path.exists(DB):
        return (None, None)
    con = connect()
    if on_date:
        row = con.execute("""SELECT rate, date FROM ecb_fx
            WHERE currency=? AND date<=? ORDER BY date DESC LIMIT 1""",
            (currency, on_date)).fetchone()
    else:
        row = con.execute("""SELECT rate, date FROM ecb_fx
            WHERE currency=? ORDER BY date DESC LIMIT 1""", (currency,)).fetchone()
    con.close()
    return (row["rate"], row["date"]) if row else (None, None)


def latest_asof():
    """Most recent cached ECB date, or None if nothing fetched yet."""
    if not os.path.exists(DB):
        return None
    con = connect()
    row = con.execute("SELECT MAX(date) d FROM ecb_fx").fetchone()
    con.close()
    return row["d"] if row else None


if __name__ == "__main__":
    # CLI: python3 ecb_rates.py   (fetches the 90-day history)
    try:
        info = fetch_and_store()
        print(f"fetched ECB rates: as-of {info['asof']}, {info['days']} days, "
              f"{info['rows']} rows, currencies: {', '.join(info['currencies'][:12])}...")
    except RuntimeError as e:
        print("FETCH FAILED:", e)
