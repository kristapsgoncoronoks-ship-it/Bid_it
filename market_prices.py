"""
MARKET PRICES - pull official / open-data fuel-price benchmarks from the internet
and load them into pricing_intelligence.wholesale_prices (the external market index
behind the "margin vs wholesale" / "gap vs market" baseline on the Pricing page).

We deliberately use LEGITIMATE public sources, not competitor-site scraping:
  * EU Weekly Oil Bulletin (European Commission) - official weekly NET (without
    taxes) prices per member state.
  * National open-data price portals (configured per deployment).

Because the exact upstream export differs by country, the source URLs are
configurable via environment variables (so the operator points at the official
feed they're entitled to use); sensible defaults document the EU Oil Bulletin.

  MARKET_JSON_URL   JSON array of {country, date, net_price[, product_group]}
  MARKET_CSV_URL    CSV with columns: country,date,net_price[,product_group]
  MARKET_SOURCES    comma list to order/limit sources: json,csv
  MARKET_TIMEOUT    request timeout seconds (default 15)

Each source returns rows of (country, date, net_price) in NET EUR/L for diesel.
Sources are tried in order; the first that returns data wins; if all fail (or a
network is locked down) fetch_and_store raises a clean error and the admin can
upload a wholesale CSV instead (Pricing page). Prices are NET EUR/L, final.
"""
import csv
import io
import os
import json
import datetime

import requests

import pricing_intelligence

# Map ISO codes / common names to the country names used in the system.
COUNTRY_ALIASES = {
    "BE": "Belgium", "DE": "Germany", "PL": "Poland", "SE": "Sweden",
    "ES": "Spain", "DK": "Denmark", "FR": "France", "IT": "Italy",
    "AT": "Austria", "SI": "Slovenia", "LT": "Lithuania", "LV": "Latvia",
    "EE": "Estonia", "CZ": "Czechia", "NL": "Netherlands", "FI": "Finland",
}


def normalize_country(c):
    if not c:
        return c
    c = str(c).strip()
    if c.upper() in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[c.upper()]
    return c.title() if c.islower() or c.isupper() else c


def _norm_rows(raw, source):
    """Normalize parsed (country, date, price[, pg]) tuples into wholesale dicts,
    keeping only diesel and valid numbers."""
    out, today = [], str(datetime.date.today())
    for r in raw:
        country = normalize_country(r.get("country"))
        date = r.get("date") or today
        pg = (r.get("product_group") or "Diesel")
        if str(pg).lower() not in ("diesel", "gas oil", "gasoil", "gas_oil"):
            continue
        try:
            price = float(r["net_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if country and price > 0:
            out.append({"country": country, "date": date,
                        "net_price": price, "source": source})
    return out


# ---------------------------------------------------------------- parsers
def parse_json(text):
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("results") or data.get("rows") or data.get("data") or []
    return [{"country": d.get("country") or d.get("cnty") or d.get("ctry"),
             "date": d.get("date") or d.get("week"),
             "net_price": d.get("net_price") or d.get("price") or d.get("diesel"),
             "product_group": d.get("product_group") or d.get("product") or "Diesel"}
            for d in data]


def parse_csv(text):
    rows = []
    rdr = csv.reader(io.StringIO(text))
    header = None
    for i, raw in enumerate(rdr):
        cells = [c.strip() for c in raw]
        if not any(cells):
            continue
        if i == 0 and any(h.lower() in ("country", "date", "net_price", "price", "diesel") for h in cells):
            header = [h.lower() for h in cells]
            continue
        if header:
            d = dict(zip(header, cells))
            rows.append({"country": d.get("country"),
                         "date": d.get("date"),
                         "net_price": d.get("net_price") or d.get("price") or d.get("diesel"),
                         "product_group": d.get("product_group") or "Diesel"})
        else:  # headerless: country,date,price
            if len(cells) >= 3:
                rows.append({"country": cells[0], "date": cells[1], "net_price": cells[2]})
    return rows


# ---------------------------------------------------------------- sources
def _src_json(timeout):
    url = os.environ.get("MARKET_JSON_URL")
    if not url:
        raise RuntimeError("MARKET_JSON_URL not configured")
    r = requests.get(url, timeout=timeout); r.raise_for_status()
    rows = _norm_rows(parse_json(r.text), "open-data (json)")
    if not rows:
        raise RuntimeError("JSON source returned no diesel rows")
    return rows


def _src_csv(timeout):
    # default documents the EU Weekly Oil Bulletin; override with MARKET_CSV_URL
    url = os.environ.get("MARKET_CSV_URL")
    if not url:
        raise RuntimeError("MARKET_CSV_URL not configured (e.g. an EU Oil Bulletin "
                           "'prices without taxes' CSV export)")
    r = requests.get(url, timeout=timeout); r.raise_for_status()
    rows = _norm_rows(parse_csv(r.text), "EU/open-data (csv)")
    if not rows:
        raise RuntimeError("CSV source returned no diesel rows")
    return rows


SOURCES = {"json": _src_json, "csv": _src_csv}
DEFAULT_ORDER = ["json", "csv"]


def fetch_and_store(timeout=None, sources=None):
    """Scrape the first working configured source and load diesel NET prices into
    pricing_intelligence.wholesale_prices. Returns dict(rows, countries, asof, source).
    Raises RuntimeError naming every attempt if all sources fail."""
    timeout = timeout or float(os.environ.get("MARKET_TIMEOUT", "15"))
    if sources is None:
        env = os.environ.get("MARKET_SOURCES", "")
        sources = [s.strip() for s in env.split(",") if s.strip()] or DEFAULT_ORDER
    errors = []
    for key in sources:
        fn = SOURCES.get(key)
        if not fn:
            continue
        try:
            rows = fn(timeout)
            n = pricing_intelligence.load_wholesale(rows)
            return {"rows": n, "countries": sorted({r["country"] for r in rows}),
                    "asof": max(r["date"] for r in rows), "source": rows[0]["source"]}
        except Exception as e:  # noqa: BLE001 - report and try the next source
            errors.append(f"{key}: {str(e)[:140]}")
    raise RuntimeError("all market-price sources failed -> " + " | ".join(errors)
                       + " (configure MARKET_JSON_URL / MARKET_CSV_URL, or upload a "
                         "wholesale CSV on the Pricing page)")


if __name__ == "__main__":
    try:
        info = fetch_and_store()
        print(f"loaded {info['rows']} market prices via {info['source']} "
              f"(as of {info['asof']}, {len(info['countries'])} countries)")
    except RuntimeError as e:
        print("FETCH FAILED:", e)
