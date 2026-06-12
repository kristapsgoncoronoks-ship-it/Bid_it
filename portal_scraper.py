"""
DYNAMIC CLIENT-PORTAL PRICE SCRAPING — pull fuel prices from the client's OWN
authorized supplier portals (the accounts our entities hold at Q8/BP/DKV/E100/…) and
load them into pricing_intelligence.my_prices, automating what is otherwise a manual
CSV upload of the price benchmark.

LEGITIMACY: this automates retrieval of data the account holder is entitled to — the
prices on their own portal/account — using credentials they provide. Use it only for
portals you are authorized to access; respect each portal's terms of service and rate
limits. (It is NOT a tool for scraping third-party/competitor websites.)

DYNAMIC & PLUGGABLE: portals differ, so adapters are pluggable.
  • Simple portals are added with NO CODE via a JSON config:
      - kind="http_json": a login endpoint + a price endpoint + a field map
      - kind="csv":        an authenticated CSV-export URL + a column map
  • Complex portals (multi-step login, JS) get a small adapter class registered under
    the supplier code with @register("CODE").
  • kind="demo" is a built-in offline adapter (fixtures) for testing/trials.
The same orchestrator, scrape(), runs any of them.

SECURITY: portal credentials are encrypted at rest (Fernet; key derived from the app
secret key) in portal.db (git-ignored). Only an admin sets them; every scrape run is
recorded in portal_runs and audited.

OFFLINE-SAFE: live portals need outbound network; failures are caught, recorded on the
run, and surfaced — nothing crashes (mirrors market_prices.py). This sandboxed
environment has egress disabled, so live adapters are exercised via the demo adapter;
the HTTP/CSV adapters run for real once deployed with network access.

CLI:  python portal_scraper.py --scrape <SUPPLIER> <ENTITY>
      python portal_scraper.py --list
"""
import os, sqlite3, json, csv, io, datetime

import audit
import db_tuning

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("PORTAL_DB", f"{WORKDIR}/portal.db")
DEFAULT_TIMEOUT = int(os.environ.get("PORTAL_TIMEOUT", "60"))

_SCHEMA_READY = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_configs (
    supplier TEXT PRIMARY KEY,
    kind TEXT,                         -- demo | http_json | csv | custom
    base_url TEXT,
    config TEXT,                       -- JSON: endpoints, field/column map, params
    enabled INTEGER DEFAULT 1,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS portal_credentials (
    supplier TEXT, entity TEXT,
    username TEXT, secret_enc BLOB,    -- encrypted at rest
    extra TEXT,                        -- optional JSON (e.g. account id), encrypted
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (supplier, entity));
CREATE TABLE IF NOT EXISTS portal_runs (
    id INTEGER PRIMARY KEY,
    supplier TEXT, entity TEXT,
    started TEXT DEFAULT CURRENT_TIMESTAMP, finished TEXT,
    status TEXT,                       -- running | ok | failed
    rows INTEGER DEFAULT 0, message TEXT);
"""


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)
    audit.bind(con)   # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        audit.install_audit(con, ["portal_configs", "portal_credentials"])
        if DB != ":memory:":
            _SCHEMA_READY.add(DB)
            try: os.chmod(DB, 0o600)            # secrets live here
            except OSError: pass
    return con


# ---------------------------------------------------------------- credential crypto
def _fernet():
    """Symmetric key derived from the app secret key, so credentials are encrypted at
    rest without managing a separate key. Requires `cryptography`."""
    from cryptography.fernet import Fernet
    import base64, hashlib, auth
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(auth.secret_key()).digest()))

def _encrypt(text):
    return _fernet().encrypt((text or "").encode("utf-8"))

def _decrypt(blob):
    if blob is None:
        return ""
    return _fernet().decrypt(bytes(blob)).decode("utf-8")


# ---------------------------------------------------------------- config & credentials
def set_config(supplier, kind, base_url="", config=None, enabled=True):
    con = connect()
    con.execute("""INSERT INTO portal_configs (supplier, kind, base_url, config, enabled, updated_at)
                   VALUES (?,?,?,?,?, CURRENT_TIMESTAMP)
                   ON CONFLICT(supplier) DO UPDATE SET kind=excluded.kind,
                     base_url=excluded.base_url, config=excluded.config,
                     enabled=excluded.enabled, updated_at=CURRENT_TIMESTAMP""",
                (supplier.upper(), kind, base_url, json.dumps(config or {}), 1 if enabled else 0))
    con.commit(); con.close()

def get_config(supplier):
    con = connect()
    r = con.execute("SELECT * FROM portal_configs WHERE supplier=?", (supplier.upper(),)).fetchone()
    con.close()
    if not r:
        return None
    d = dict(r)
    d["config"] = json.loads(d["config"] or "{}")
    d["enabled"] = bool(d["enabled"])
    return d

def list_configs():
    con = connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM portal_configs ORDER BY supplier")]
    con.close()
    for d in rows:
        d["enabled"] = bool(d["enabled"])
    return rows

def set_credentials(supplier, entity, username, secret, extra=None):
    """Store (encrypted) the login for an entity's account on a supplier portal."""
    con = connect()
    con.execute("""INSERT INTO portal_credentials (supplier, entity, username, secret_enc, extra, updated_at)
                   VALUES (?,?,?,?,?, CURRENT_TIMESTAMP)
                   ON CONFLICT(supplier, entity) DO UPDATE SET username=excluded.username,
                     secret_enc=excluded.secret_enc, extra=excluded.extra,
                     updated_at=CURRENT_TIMESTAMP""",
                (supplier.upper(), entity, username, _encrypt(secret),
                 _encrypt(json.dumps(extra)) if extra is not None else None))
    con.commit(); con.close()

def get_credentials(supplier, entity):
    con = connect()
    r = con.execute("SELECT * FROM portal_credentials WHERE supplier=? AND entity=?",
                    (supplier.upper(), entity)).fetchone()
    con.close()
    if not r:
        return None
    out = {"supplier": r["supplier"], "entity": r["entity"], "username": r["username"],
           "secret": _decrypt(r["secret_enc"]), "base_url": (get_config(supplier) or {}).get("base_url", "")}
    if r["extra"]:
        try: out["extra"] = json.loads(_decrypt(r["extra"]))
        except Exception: out["extra"] = None
    return out

def delete_credentials(supplier, entity):
    con = connect()
    con.execute("DELETE FROM portal_credentials WHERE supplier=? AND entity=?",
                (supplier.upper(), entity))
    con.commit(); con.close()

def list_portals():
    """For the UI: each configured portal + whether credentials exist (NO secrets) +
    the last run."""
    con = connect()
    creds = {(r["supplier"], r["entity"]) for r in
             con.execute("SELECT supplier, entity FROM portal_credentials")}
    runs = {}
    for r in con.execute("SELECT supplier, entity, status, finished, rows, message FROM portal_runs "
                         "ORDER BY id"):
        runs[(r["supplier"], r["entity"])] = dict(r)
    cfgs = list_configs()
    con.close()
    out = []
    for c in cfgs:
        ents = sorted({e for (s, e) in creds if s == c["supplier"]}) or [None]
        for ent in ents:
            out.append({"supplier": c["supplier"], "kind": c["kind"], "enabled": c["enabled"],
                        "entity": ent, "has_creds": (c["supplier"], ent) in creds,
                        "last_run": runs.get((c["supplier"], ent))})
    return out


# ---------------------------------------------------------------- adapters
class PortalAdapter:
    """Base adapter. fetch() returns a list of dict rows:
       {country, city, date 'YYYY-MM-DD', product_group, net_price (NET EUR/L), currency}."""
    def fetch(self, creds, cfg, date_from, date_to):
        raise NotImplementedError

ADAPTERS = {}   # SUPPLIER_CODE -> adapter instance (custom code adapters)

def register(code):
    def deco(cls):
        ADAPTERS[code.upper()] = cls()
        return cls
    return deco


def _dig(obj, dotted):
    """Pull a nested value by 'a.b.c' from JSON (lists indexable by int)."""
    cur = obj
    for part in str(dotted).split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


class HttpJsonAdapter(PortalAdapter):
    """Config-driven JSON portal. cfg['config'] keys:
       login_url, login_method ('POST'), login_fields {field: 'username'|'secret'|literal},
       token_path (dotted path to a bearer token, optional),
       price_url, price_method ('GET'), price_params {..., '_from','_to' placeholders},
       rows_path (dotted to the list), map {country,city,date,net_price,product_group,currency}.
    """
    def fetch(self, creds, cfg, date_from, date_to):
        import requests
        c = cfg["config"]; base = cfg.get("base_url", "") or ""
        s = requests.Session()
        headers = {}
        if c.get("login_url"):
            fields = {k: (creds.get(v, v) if isinstance(v, str) else v)
                      for k, v in (c.get("login_fields") or {}).items()}
            lr = s.request(c.get("login_method", "POST"), base + c["login_url"],
                           json=fields, timeout=DEFAULT_TIMEOUT)
            lr.raise_for_status()
            if c.get("token_path"):
                tok = _dig(lr.json(), c["token_path"])
                headers["Authorization"] = f"Bearer {tok}"
        params = dict(c.get("price_params") or {})
        for k, v in params.items():
            if v == "_from": params[k] = date_from
            if v == "_to":   params[k] = date_to
        pr = s.request(c.get("price_method", "GET"), base + c["price_url"],
                       params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
        pr.raise_for_status()
        data = pr.json()
        rows = _dig(data, c["rows_path"]) if c.get("rows_path") else data
        m = c.get("map") or {}
        out = []
        for row in (rows or []):
            out.append({k: _dig(row, m[k]) if k in m else None
                        for k in ("country", "city", "date", "net_price", "product_group", "currency")})
        return out


class CsvExportAdapter(PortalAdapter):
    """Config-driven CSV export. cfg['config'] keys:
       csv_url, auth ('basic'|'none'), params {...'_from','_to'}, columns {target: 'CsvHeader'}.
    """
    def fetch(self, creds, cfg, date_from, date_to):
        import requests
        c = cfg["config"]; base = cfg.get("base_url", "") or ""
        auth_tuple = (creds.get("username"), creds.get("secret")) if c.get("auth") == "basic" else None
        params = dict(c.get("params") or {})
        for k, v in params.items():
            if v == "_from": params[k] = date_from
            if v == "_to":   params[k] = date_to
        r = requests.get(base + c["csv_url"], params=params, auth=auth_tuple, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        cols = c.get("columns") or {}
        out = []
        for row in csv.DictReader(io.StringIO(r.text)):
            out.append({k: (row.get(cols[k]) if k in cols else None)
                        for k in ("country", "city", "date", "net_price", "product_group", "currency")})
        return out


class DemoAdapter(PortalAdapter):
    """Offline adapter: returns the fixture rows in cfg['config']['rows'] (or a default),
    so the whole pipeline is testable without network. Used for trials and tests."""
    def fetch(self, creds, cfg, date_from, date_to):
        rows = (cfg.get("config") or {}).get("rows")
        if rows is None:
            today = datetime.date.today().isoformat()
            rows = [{"country": "Germany", "city": "Demo City", "date": today,
                     "net_price": 1.42, "product_group": "Diesel", "currency": "EUR"}]
        return list(rows)


_BUILTIN = {"http_json": HttpJsonAdapter, "csv": CsvExportAdapter, "demo": DemoAdapter}

def _adapter_for(supplier, cfg):
    if supplier.upper() in ADAPTERS:           # a registered custom code adapter wins
        return ADAPTERS[supplier.upper()]
    cls = _BUILTIN.get(cfg.get("kind"))
    if not cls:
        raise RuntimeError(f"no adapter for portal kind '{cfg.get('kind')}' (supplier {supplier})")
    return cls()


# ---------------------------------------------------------------- orchestrator
def _normalize(rows):
    """Coerce adapter rows to clean my_prices rows; drop anything unusable."""
    out = []
    for r in rows or []:
        try:
            country = (r.get("country") or "").strip()
            city = (r.get("city") or "").strip()
            date = str(r.get("date") or "").strip()[:10]
            price = float(r.get("net_price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (country and date and price > 0):
            continue
        out.append({"country": country, "city": city, "date": date,
                    "net_price": price, "product_group": (r.get("product_group") or "Diesel").strip()})
    return out

def _start_run(con, supplier, entity):
    cur = con.execute("INSERT INTO portal_runs (supplier, entity, status) VALUES (?,?, 'running')",
                      (supplier.upper(), entity))
    con.commit()
    return cur.lastrowid

def _finish_run(con, run_id, status, rows, message):
    con.execute("UPDATE portal_runs SET finished=CURRENT_TIMESTAMP, status=?, rows=?, message=? WHERE id=?",
                (status, rows, (message or "")[:500], run_id))
    con.commit()

def scrape(supplier, entity, date_from=None, date_to=None):
    """Scrape one portal account and load the prices into the MY-Prices benchmark
    (source 'portal:<SUPPLIER>'). Returns a summary dict; records the run; never
    leaves a half-written state (load is its own transaction in pricing_intelligence).
    Raises RuntimeError on misconfiguration or an upstream failure (recorded first)."""
    supplier = supplier.upper()
    cfg = get_config(supplier)
    if not cfg:
        raise RuntimeError(f"portal '{supplier}' is not configured")
    if not cfg["enabled"]:
        raise RuntimeError(f"portal '{supplier}' is disabled")
    creds = get_credentials(supplier, entity)
    if creds is None and cfg["kind"] != "demo":
        raise RuntimeError(f"no stored credentials for {supplier} / {entity}")
    adapter = _adapter_for(supplier, cfg)
    con = connect()
    run_id = _start_run(con, supplier, entity)
    try:
        raw = adapter.fetch(creds or {}, cfg, date_from, date_to)
        rows = _normalize(raw)
        import pricing_intelligence as PI
        n = PI.load_my_prices(rows, source=f"portal:{supplier}")
        _finish_run(con, run_id, "ok", n, f"{n} price row(s) loaded from {len(raw or [])} fetched")
        con.close()
        return {"supplier": supplier, "entity": entity, "fetched": len(raw or []), "loaded": n}
    except Exception as e:
        _finish_run(con, run_id, "failed", 0, f"{type(e).__name__}: {e}")
        con.close()
        raise


# ---------------------------------------------------------------- CLI / self-test
if __name__ == "__main__":
    import sys
    if "--scrape" in sys.argv:
        i = sys.argv.index("--scrape")
        sup, ent = sys.argv[i + 1], sys.argv[i + 2]
        print(scrape(sup, ent))
    elif "--list" in sys.argv:
        for p in list_portals():
            lr = p["last_run"]
            print(f"  {p['supplier']:8} {str(p['entity'] or '-'):14} kind={p['kind']:9} "
                  f"creds={'yes' if p['has_creds'] else 'NO':3} "
                  f"last={(lr['status'] + ' ' + str(lr['rows'])) if lr else '-'}")
    else:
        # offline self-test with the demo adapter
        import tempfile, shutil
        d = tempfile.mkdtemp()
        DB = os.path.join(d, "portal.db"); _SCHEMA_READY.clear()
        import pricing_intelligence as PI
        PI.DB = os.path.join(d, "fuel_history.db")
        set_config("DEMO", "demo", config={"rows": [
            {"country": "Germany", "city": "Berlin", "date": "2026-05-10",
             "net_price": 1.41, "product_group": "Diesel", "currency": "EUR"},
            {"country": "Poland", "city": "Warsaw", "date": "2026-05-10",
             "net_price": 1.33, "product_group": "Diesel", "currency": "EUR"}]})
        set_credentials("DEMO", "JUPITER", "user@example.com", "s3cret")
        assert get_credentials("DEMO", "JUPITER")["secret"] == "s3cret", "crypto round-trip"
        res = scrape("DEMO", "JUPITER")
        print("scraped:", res)
        assert res["loaded"] == 2, res
        shutil.rmtree(d, ignore_errors=True)
        print("portal_scraper self-test OK")
