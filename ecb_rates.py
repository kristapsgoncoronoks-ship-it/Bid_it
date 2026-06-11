"""
ECB REFERENCE RATES - scrape euro foreign-exchange reference rates from the
internet and cache them locally (ecb_rates.db) so the FX-comparison page can
check each invoice's effective exchange rate against the official rate.

Rates are stored as "foreign units per 1 EUR" (the ECB convention: 1 EUR = rate),
which matches the invoice implied rate net_local / net_eur, so the two compare
directly.

RESILIENT SCRAPING
------------------
Networks differ in what they allow, so fetch_and_store() tries several public
sources in order and uses the first that responds; all are euro-reference based
(the first two are the ECB itself, the rest are ECB-derived mirrors):

  1. ECB eurofxref XML        www.ecb.europa.eu      (authoritative, ~90 days)
  2. ECB SDMX data API (JSON) data-api.ecb.europa.eu (authoritative)
  3. Frankfurter (ECB data)   api.frankfurter.app    (90-day time series)
  4. exchangerate.host        api.exchangerate.host  (latest)
  5. open ER-API              open.er-api.com        (latest)

No API key is needed. Override order/timeout with env vars ECB_SOURCES (comma
list of the keys ecb_xml,ecb_api,frankfurter,exchangerate_host,er_api) and
ECB_TIMEOUT. If every source fails, fetch_and_store raises a RuntimeError that
names what was tried, which the UI shows; cached rates keep working.

NOTE: the scrape needs outbound HTTPS. In a locked-down environment (e.g. an
allow-list network policy) it will report every source as unreachable until the
policy permits at least one of the hosts above.
"""
import datetime
import os
import sqlite3
import xml.etree.ElementTree as ET

import requests

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/ecb_rates.db"

DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
HIST_90D_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
# currencies we care about most (others are stored too when a source returns them)
CCY = ["USD", "GBP", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON", "BGN"]


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS ecb_fx (
        date TEXT, currency TEXT, rate REAL, source TEXT,
        PRIMARY KEY (date, currency))""")
    try: con.execute("ALTER TABLE ecb_fx ADD COLUMN source TEXT")
    except sqlite3.OperationalError: pass  # column already exists (safe)
    return con


# ---------------------------------------------------------------- parsers
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


# ---------------------------------------------------------------- sources
# Each source returns a list of (date, currency, rate). Raises on failure.
def _src_ecb_xml(timeout):
    for url in (HIST_90D_URL, DAILY_URL):
        try:
            r = requests.get(url, timeout=timeout); r.raise_for_status()
            rows = _parse(r.text)
            if rows:
                return rows
        except requests.RequestException:
            continue
    raise RuntimeError("ECB eurofxref XML returned no usable data")


def _src_ecb_api(timeout):
    # SDMX JSON: one series per currency, daily, last ~90 obs
    syms = ",".join(CCY)
    url = (f"https://data-api.ecb.europa.eu/service/data/EXR/D.{syms}.EUR.SP00.A"
           f"?lastNObservations=90&format=jsondata")
    r = requests.get(url, timeout=timeout); r.raise_for_status()
    j = r.json()
    ds = j["structure"]["dimensions"]
    cur_vals = next(d["values"] for d in ds["series"] if d["id"] == "CURRENCY")
    dates = [o["id"] for o in next(d["values"] for d in ds["observation"] if d["id"] == "TIME_PERIOD")]
    rows = []
    for key, series in j["dataSets"][0]["series"].items():
        cur = cur_vals[int(key.split(":")[1])]["id"]
        for oi, val in series["observations"].items():
            if val and val[0] is not None:
                rows.append((dates[int(oi)], cur, float(val[0])))
    if not rows:
        raise RuntimeError("ECB SDMX API returned no observations")
    return rows


def _src_frankfurter(timeout):
    end = datetime.date.today()
    start = end - datetime.timedelta(days=90)
    url = f"https://api.frankfurter.app/{start}..{end}?base=EUR"
    r = requests.get(url, timeout=timeout); r.raise_for_status()
    j = r.json()
    rows = [(d, c, float(v)) for d, day in j.get("rates", {}).items()
            for c, v in day.items()]
    if not rows:
        raise RuntimeError("Frankfurter returned no rates")
    return rows


def _src_exchangerate_host(timeout):
    url = "https://api.exchangerate.host/latest?base=EUR"
    r = requests.get(url, timeout=timeout); r.raise_for_status()
    j = r.json()
    d = j.get("date") or str(datetime.date.today())
    rows = [(d, c, float(v)) for c, v in j.get("rates", {}).items()]
    if not rows:
        raise RuntimeError("exchangerate.host returned no rates")
    return rows


def _src_er_api(timeout):
    url = "https://open.er-api.com/v6/latest/EUR"
    r = requests.get(url, timeout=timeout); r.raise_for_status()
    j = r.json()
    try:
        d = datetime.datetime.strptime(
            j["time_last_update_utc"], "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
    except (KeyError, ValueError):
        d = str(datetime.date.today())
    rows = [(d, c, float(v)) for c, v in j.get("rates", {}).items()]
    if not rows:
        raise RuntimeError("er-api returned no rates")
    return rows


SOURCES = {
    "ecb_xml":            ("ECB (eurofxref)",      _src_ecb_xml),
    "ecb_api":            ("ECB (SDMX API)",       _src_ecb_api),
    "frankfurter":        ("Frankfurter (ECB)",    _src_frankfurter),
    "exchangerate_host":  ("exchangerate.host",    _src_exchangerate_host),
    "er_api":             ("open ER-API",          _src_er_api),
}
DEFAULT_ORDER = ["ecb_xml", "ecb_api", "frankfurter", "exchangerate_host", "er_api"]


# ---------------------------------------------------------------- orchestrator
def fetch_and_store(timeout=None, sources=None, retries=2):
    """Scrape ECB rates from the internet (first working source) and upsert into
    the cache. Returns dict(asof, days, rows, currencies, source). Raises
    RuntimeError naming every attempt if all sources fail."""
    timeout = timeout or float(os.environ.get("ECB_TIMEOUT", "12"))
    if sources is None:
        env = os.environ.get("ECB_SOURCES", "")
        sources = [s.strip() for s in env.split(",") if s.strip()] or DEFAULT_ORDER
    errors = []
    for key in sources:
        if key not in SOURCES:
            continue
        label, fn = SOURCES[key]
        for attempt in range(retries):
            try:
                rows = fn(timeout)
                return _store(rows, label)
            except Exception as e:  # noqa: BLE001 - report any source failure, try next
                errors.append(f"{label}: {str(e)[:120]}")
                break  # a parse/format error won't fix on retry; move to next source
    raise RuntimeError("all ECB sources failed -> " + " | ".join(errors))


def _store(rows, source):
    con = connect()
    con.executemany(
        "INSERT OR REPLACE INTO ecb_fx (date, currency, rate, source) VALUES (?,?,?,?)",
        [(d, c, r, source) for d, c, r in rows])
    days = sorted({d for d, _, _ in rows})
    con.executemany("INSERT OR REPLACE INTO ecb_fx (date, currency, rate, source) VALUES (?,?,1.0,?)",
                    [(d, "EUR", source) for d in days])
    con.commit(); con.close()
    return {"asof": days[-1] if days else None, "days": len(days), "rows": len(rows),
            "currencies": sorted({c for _, c, _ in rows}), "source": source}


def store(rows, source="manual upload"):
    """Public: persist (date, currency, rate) rows from a manual upload."""
    if not rows:
        raise ValueError("no rows to store")
    return _store(rows, source)


_DATE_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_csv(text):
    """Parse an uploaded rate file into (date, currency, rate) rows. Accepts:
        date,currency,rate        e.g.  2026-05-31,PLN,4.31
        currency,rate             (uses today's date)
    A header row is auto-detected/skipped. Rate is foreign units per 1 EUR."""
    import csv
    import io
    today = str(datetime.date.today())
    rows = []
    for i, raw in enumerate(csv.reader(io.StringIO(text))):
        cells = [c.strip() for c in raw if c.strip() != ""]
        if not cells:
            continue
        if i == 0 and any(h.lower() in ("date", "currency", "ccy", "rate") for h in cells):
            continue  # header
        try:
            if len(cells) >= 3:
                d, c, rate = cells[0], cells[1].upper(), float(cells[2])
            elif len(cells) == 2:
                d, c, rate = today, cells[0].upper(), float(cells[1])
            else:
                continue
        except ValueError:
            continue
        if not _DATE_RE.match(d):
            d = today
        if 1 <= len(c) <= 3:
            rows.append((d, c, rate))
    if not rows:
        raise ValueError("no valid rows found (expected columns: date,currency,rate)")
    return rows


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
    """(date, source) of the most recent cached rate, or (None, None)."""
    if not os.path.exists(DB):
        return (None, None)
    con = connect()
    row = con.execute("SELECT date, source FROM ecb_fx ORDER BY date DESC LIMIT 1").fetchone()
    con.close()
    return (row["date"], row["source"]) if row else (None, None)


if __name__ == "__main__":
    # CLI: python3 ecb_rates.py   (scrapes from the internet, first working source)
    try:
        info = fetch_and_store()
        print(f"scraped ECB rates via {info['source']}: as-of {info['asof']}, "
              f"{info['days']} day(s), {info['rows']} rows, "
              f"currencies: {', '.join(info['currencies'][:12])}...")
    except RuntimeError as e:
        print("SCRAPE FAILED:", e)
