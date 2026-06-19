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

SECURITY: portal credentials are encrypted at rest with ENVELOPE ENCRYPTION
(`keyvault.py`: a random per-secret DEK, wrapped by a pluggable KEK — local master key
now, KMS/BYOK later), AAD-bound to their (supplier, entity) row, in portal.db
(git-ignored). Legacy single-key Fernet blobs still decrypt for backward compatibility;
`reencrypt_legacy()` migrates them. Only an admin sets credentials; every scrape run is
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
import db_migrate
import applog
import keyvault
import tenancy

log = applog.get("portal_scraper")

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
    username TEXT, secret_enc BLOB,    -- envelope ciphertext (BLOB: audit-excluded)
    extra BLOB,                        -- optional JSON (e.g. account id), envelope ciphertext
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (supplier, entity));
CREATE TABLE IF NOT EXISTS portal_runs (
    id INTEGER PRIMARY KEY,
    supplier TEXT, entity TEXT,
    started TEXT DEFAULT CURRENT_TIMESTAMP, finished TEXT,
    status TEXT,                       -- running | ok | failed
    rows INTEGER DEFAULT 0, message TEXT);
"""


def _upgrade_extra_to_blob(con):
    """Backward-compat: early portal.db files declared `extra` as TEXT. It now holds
    BINARY envelope ciphertext (like `secret_enc`), and the audit layer excludes a
    column from its JSON snapshot ONLY when it is declared BLOB — a binary value in a
    TEXT-declared column breaks the audit `json_object()` trigger. Rebuild the table so
    `extra` is BLOB (audit-excluded) before audit triggers are (re)installed. One-time,
    idempotent: skipped once `extra` is already BLOB."""
    info = con.execute("PRAGMA table_info(portal_credentials)").fetchall()
    if not info:
        return                                  # fresh DB: SCHEMA already made it BLOB
    extra = next((r for r in info if r[1] == "extra"), None)
    if extra is None or (extra[2] or "").upper() == "BLOB":
        return
    # Drop the stale audit triggers (they embed the old column set) so install_audit
    # rebuilds them against the new BLOB column; then rebuild the table in place.
    for sfx in ("i", "u", "d"):
        con.execute(f"DROP TRIGGER IF EXISTS aud_portal_credentials_{sfx}")
    con.executescript("""
        CREATE TABLE portal_credentials_new (
            supplier TEXT, entity TEXT,
            username TEXT, secret_enc BLOB, extra BLOB,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (supplier, entity));
        INSERT INTO portal_credentials_new (supplier, entity, username, secret_enc, extra, updated_at)
            SELECT supplier, entity, username, secret_enc, extra, updated_at FROM portal_credentials;
        DROP TABLE portal_credentials;
        ALTER TABLE portal_credentials_new RENAME TO portal_credentials;""")
    con.commit()
    log.info("_upgrade_extra_to_blob: migrated portal_credentials.extra TEXT->BLOB on %s", DB)


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)
    audit.bind(con)   # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        _upgrade_extra_to_blob(con)
        # APPEND-ONLY migration list (positions are stable; never reorder/delete).
        db_migrate.apply(con, "portal_scraper", [
            # per-portal scheduled-pull interval; 0 = scheduling OFF (the default). The
            # off-by-default scheduler (app._scrape_tick) only enqueues a fetch when this
            # is > 0 for an enabled portal that has stored credentials.
            "ALTER TABLE portal_configs ADD COLUMN interval_hours REAL DEFAULT 0",
            # P1 multi-tenancy (schema plumbing only): stamp the portal tables with a
            # tenant_id; existing rows backfill to DEFAULT_TENANT_ID via the column
            # DEFAULT, new rows default too. NO query reads this column yet (the
            # `multitenant` switch is OFF and scope_clause is unwired until P2), so
            # this is a pure no-behavior-change addition. tenant_id is a plain TEXT
            # column ALONGSIDE the envelope-encrypted secret_enc/extra BLOBs — it does
            # not touch the credential crypto. TEXT is audit-safe (the audited
            # portal_configs/portal_credentials keep their triggers; BLOB cols stay
            # excluded). APPEND-ONLY — keep at END (runs after _upgrade_extra_to_blob).
            *tenancy.tenant_column_ddls([
                "portal_configs", "portal_credentials", "portal_runs",
            ]),
            # ── PK RE-KEY (multi-tenant): tenant-qualified PRIMARY KEYs ──────────
            # portal_configs (PK supplier) and portal_credentials (PK (supplier,
            # entity)) carry a tenant_id column (P1, the spread above) and tenant-
            # scoped reads / stamped writes (P2), but their PRIMARY KEYs did NOT
            # include tenant_id — so set_config/set_credentials' INSERT … ON CONFLICT
            # resolves on the NATURAL key only. Under the `multitenant` switch ON two
            # tenants writing the SAME supplier/(supplier, entity) would COLLIDE and
            # overwrite each other's stored portal SECRETS — cross-tenant data loss.
            #
            # SQLite cannot ALTER a PRIMARY KEY in place, so each table is REBUILT:
            # create a __rekey twin, copy all rows (explicit column list — incl. the
            # envelope-encrypted secret_enc/extra BLOBs, copied verbatim), drop the
            # old, rename the twin. Runs once per DB (versioned), supersedes the PK on
            # both fresh and existing portal.db files. APPEND-ONLY — keep at END.
            #
            # AUDITED-DB SPECIFICS (these tables ARE audited; benchmark.db was not):
            #   • AUDIT ROWKEY PRESERVED. audit._cols_pk returns pks[0] = the FIRST
            #     pk-flagged column in COLUMN-DEFINITION order (not PK-clause order),
            #     and the trigger logs NEW.{pk}. We define tenant_id as the LAST column
            #     but FIRST in the PRIMARY KEY clause, so pks[0] stays `supplier` and
            #     the audit rowkey is UNCHANGED (NEW.supplier), not the tenant_id.
            #   • TRIGGERS REINSTATED. DROP TABLE drops aud_<t>_i/u/d. We do NOT
            #     hand-write CREATE TRIGGER here — connect() calls install_audit RIGHT
            #     AFTER db_migrate.apply, which recreates the triggers on the first
            #     connect that runs this migration (cache miss at process start). The
            #     pairing (apply → install_audit) is what keeps the audit coverage.
            #
            # OFF-by-default is byte-identical: a single 'default' tenant behaves
            # exactly as the natural PK did.

            # portal_configs: PK (supplier) -> (tenant_id, supplier). tenant_id LAST in
            # the column list (rowkey-preserving) but FIRST in the PK clause.
            """CREATE TABLE IF NOT EXISTS portal_configs__rekey (
                supplier TEXT,
                kind TEXT,
                base_url TEXT,
                config TEXT,
                enabled INTEGER DEFAULT 1,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                interval_hours REAL DEFAULT 0,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, supplier))""",
            """INSERT INTO portal_configs__rekey
                (supplier, kind, base_url, config, enabled, updated_at,
                 interval_hours, tenant_id)
                SELECT supplier, kind, base_url, config, enabled, updated_at,
                 interval_hours, tenant_id
                FROM portal_configs""",
            "DROP TABLE portal_configs",
            "ALTER TABLE portal_configs__rekey RENAME TO portal_configs",
            # (no secondary indexes existed on portal_configs — the natural PK was the
            # only one, now superseded by the tenant-qualified PK; nothing to recreate.)

            # portal_credentials: PK (supplier, entity) -> (tenant_id, supplier, entity).
            # secret_enc/extra are BLOB (audit-excluded) — copied verbatim, crypto
            # untouched. tenant_id LAST in the column list (rowkey stays NEW.supplier).
            """CREATE TABLE IF NOT EXISTS portal_credentials__rekey (
                supplier TEXT, entity TEXT,
                username TEXT, secret_enc BLOB,
                extra BLOB,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, supplier, entity))""",
            """INSERT INTO portal_credentials__rekey
                (supplier, entity, username, secret_enc, extra, updated_at, tenant_id)
                SELECT supplier, entity, username, secret_enc, extra, updated_at,
                 tenant_id
                FROM portal_credentials""",
            "DROP TABLE portal_credentials",
            "ALTER TABLE portal_credentials__rekey RENAME TO portal_credentials",
            # (no secondary indexes existed on portal_credentials either.)
        ])
        audit.install_audit(con, ["portal_configs", "portal_credentials"])
        if DB != ":memory:":
            _SCHEMA_READY.add(DB)
            try: os.chmod(DB, 0o600)            # secrets live here
            except OSError as e:
                log.warning("connect: could not restrict permissions on secrets DB %s: %s", DB, e)
    return con


# ---------------------------------------------------------------- credential crypto
# Credentials are sealed with ENVELOPE encryption (keyvault.seal/open): a random
# per-secret DEK wrapped by a pluggable KEK, AES-256-GCM, AAD-bound to the row. Blobs
# stored before this upgrade are LEGACY single-key Fernet tokens — _decrypt() detects
# them by the absence of the envelope magic and reads them via _fernet(), so no stored
# credential is lost. reencrypt_legacy() migrates them to the envelope on demand.
def _fernet():
    """LEGACY reader: the old symmetric key derived from the app secret key. Kept ONLY
    to decrypt credentials stored before the envelope upgrade. Requires `cryptography`."""
    from cryptography.fernet import Fernet
    import base64, hashlib, auth
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(auth.secret_key()).digest()))

def _encrypt(text, aad=""):
    return keyvault.seal(text or "", aad)

def _decrypt(blob, aad=""):
    if blob is None:
        return ""
    blob = bytes(blob)
    if keyvault.is_envelope(blob):
        return keyvault.open(blob, aad)
    # LEGACY: pre-envelope Fernet token (not AAD-bound — aad is ignored, as it must be).
    return _fernet().decrypt(blob).decode("utf-8")


# ---------------------------------------------------------------- config & credentials
def set_config(supplier, kind, base_url="", config=None, enabled=True, interval_hours=0):
    """Upsert a portal config. `interval_hours` is the SCHEDULED-PULL cadence — 0 (the
    default) means scheduling is OFF for this portal; > 0 makes it eligible for the
    off-by-default scheduler (only when ALSO enabled, with stored credentials, AND the
    global kill-switch is armed). Parsed defensively to a float (unparseable -> 0)."""
    try:
        ih = float(interval_hours) if interval_hours is not None else 0.0
    except (TypeError, ValueError):
        ih = 0.0
    con = connect()
    # P2: stamp the bound tenant on this user-facing admin write (DEFAULT_TENANT_ID OFF;
    # raises under ON with no concrete tenant). PK now tenant-qualified — the ON CONFLICT
    # target is (tenant_id, supplier), matching the rekeyed PRIMARY KEY (see migrations),
    # so two tenants configuring the same supplier no longer collide.
    con.execute("""INSERT INTO portal_configs
                     (supplier, kind, base_url, config, enabled, interval_hours, tenant_id, updated_at)
                   VALUES (?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
                   ON CONFLICT(tenant_id, supplier) DO UPDATE SET kind=excluded.kind,
                     base_url=excluded.base_url, config=excluded.config,
                     enabled=excluded.enabled, interval_hours=excluded.interval_hours,
                     updated_at=CURRENT_TIMESTAMP""",
                (supplier.upper(), kind, base_url, json.dumps(config or {}),
                 1 if enabled else 0, ih, tenancy.write_tenant()))
    con.commit(); con.close()

def get_config(supplier):
    con = connect()
    # P2 tenant isolation: a bound tenant sees only its own portal config (inert OFF).
    frag, params = tenancy.scope_clause()
    r = con.execute("SELECT * FROM portal_configs WHERE supplier=?" + frag,
                    [supplier.upper(), *params]).fetchone()
    con.close()
    if not r:
        return None
    d = dict(r)
    # tenant_id is internal multi-tenancy plumbing (P1), not part of the config
    # contract returned to callers/UI — keep it out of the exposed dict.
    d.pop(tenancy.TENANT_COLUMN, None)
    d["config"] = json.loads(d["config"] or "{}")
    d["enabled"] = bool(d["enabled"])
    return d

def list_configs():
    con = connect()
    # P2 tenant isolation: scope to the bound tenant (owner sees all; inert OFF). No
    # base WHERE here, so anchor on WHERE 1=1 before appending the AND-led fragment.
    frag, params = tenancy.scope_clause()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM portal_configs WHERE 1=1" + frag + " ORDER BY supplier", params)]
    con.close()
    for d in rows:
        # exclude the P1 tenant_id plumbing from the exposed config contract.
        d.pop(tenancy.TENANT_COLUMN, None)
        d["enabled"] = bool(d["enabled"])
    return rows

def _aad(supplier, entity):
    """Associated data binding a sealed blob to its (supplier, entity) row, so a blob
    cannot be replayed into a different row."""
    return f"{supplier.upper()}:{entity}"

def set_credentials(supplier, entity, username, secret, extra=None):
    """Store (envelope-encrypted, AAD-bound) the login for an entity's account on a
    supplier portal."""
    aad = _aad(supplier, entity)
    con = connect()
    # P2: stamp the bound tenant on this user-facing admin write (DEFAULT_TENANT_ID OFF;
    # raises under ON with no concrete tenant). PK now tenant-qualified — the ON CONFLICT
    # target is (tenant_id, supplier, entity), matching the rekeyed PRIMARY KEY (see
    # migrations), so two tenants storing a credential for the same (supplier, entity) no
    # longer collide/overwrite each other's secret.
    con.execute("""INSERT INTO portal_credentials (supplier, entity, username, secret_enc, extra, tenant_id, updated_at)
                   VALUES (?,?,?,?,?,?, CURRENT_TIMESTAMP)
                   ON CONFLICT(tenant_id, supplier, entity) DO UPDATE SET username=excluded.username,
                     secret_enc=excluded.secret_enc, extra=excluded.extra,
                     updated_at=CURRENT_TIMESTAMP""",
                (supplier.upper(), entity, username, _encrypt(secret, aad),
                 _encrypt(json.dumps(extra), aad) if extra is not None else None,
                 tenancy.write_tenant()))
    con.commit(); con.close()

def get_credentials(supplier, entity):
    aad = _aad(supplier, entity)
    con = connect()
    # P2 tenant isolation: a bound tenant reads only its own stored credentials —
    # the core custody-isolation guarantee (owner sees all; inert OFF).
    frag, params = tenancy.scope_clause()
    r = con.execute("SELECT * FROM portal_credentials WHERE supplier=? AND entity=?" + frag,
                    [supplier.upper(), entity, *params]).fetchone()
    con.close()
    if not r:
        return None
    out = {"supplier": r["supplier"], "entity": r["entity"], "username": r["username"],
           "secret": _decrypt(r["secret_enc"], aad),
           "base_url": (get_config(supplier) or {}).get("base_url", "")}
    if r["extra"]:
        try: out["extra"] = json.loads(_decrypt(r["extra"], aad))
        except Exception: out["extra"] = None
    return out

def delete_credentials(supplier, entity):
    con = connect()
    # P2 tenant isolation: scope the delete so one tenant cannot remove another tenant's
    # stored credential (mirrors customer_master.delete_template); inert when OFF.
    frag, params = tenancy.scope_clause()
    con.execute("DELETE FROM portal_credentials WHERE supplier=? AND entity=?" + frag,
                [supplier.upper(), entity, *params])
    con.commit(); con.close()

def reencrypt_legacy():
    """Migrate any LEGACY single-key Fernet credential blobs to the envelope (AAD-bound)
    scheme. Reads each row that has a non-envelope secret_enc/extra, decrypts via the
    legacy path, and re-stores via the envelope path. Idempotent (envelope rows are
    skipped) and per-row fault-tolerant (logs and continues — one bad row never aborts
    the migration). Reads stay read-only; this is the explicit admin migration path.
    Returns (upgraded, total)."""
    # P2 tenant isolation DEFERRED here: this is operator/owner KEK-migration maintenance
    # that must re-wrap EVERY row regardless of tenant (it re-encrypts in place, so there
    # is no cross-tenant READ leak). Scoping it would WRONGLY skip other tenants' rows.
    con = connect()
    rows = con.execute("SELECT supplier, entity, secret_enc, extra FROM portal_credentials").fetchall()
    con.close()
    upgraded = 0
    for r in rows:
        sec, ext = r["secret_enc"], r["extra"]
        sec_legacy = sec is not None and not keyvault.is_envelope(bytes(sec))
        ext_legacy = ext is not None and not keyvault.is_envelope(bytes(ext))
        if not (sec_legacy or ext_legacy):
            continue
        try:
            aad = _aad(r["supplier"], r["entity"])
            # decrypt via the appropriate path (legacy or already-envelope), then re-seal
            secret = _decrypt(sec, aad)
            extra_blob = None
            if ext is not None:
                extra_blob = _encrypt(_decrypt(ext, aad), aad)
            uc = connect()
            uc.execute("""UPDATE portal_credentials SET secret_enc=?, extra=?,
                          updated_at=CURRENT_TIMESTAMP WHERE supplier=? AND entity=?""",
                       (_encrypt(secret, aad), extra_blob, r["supplier"], r["entity"]))
            uc.commit(); uc.close()
            upgraded += 1
        except Exception as e:
            log.warning("reencrypt_legacy: skipped %s/%s: %s: %s",
                        r["supplier"], r["entity"], type(e).__name__, e)
    return upgraded, len(rows)

def list_portals():
    """For the UI: each configured portal + whether credentials exist (NO secrets) +
    the last run."""
    con = connect()
    # P2 tenant isolation: scope BOTH the credential listing and the run history to
    # the bound tenant (owner sees all; inert OFF). No base WHERE on either, so anchor
    # each on WHERE 1=1 before the AND-led fragment.
    frag, params = tenancy.scope_clause()
    creds = {(r["supplier"], r["entity"]) for r in
             con.execute("SELECT supplier, entity FROM portal_credentials WHERE 1=1" + frag,
                         params)}
    runs = {}
    for r in con.execute("SELECT supplier, entity, status, finished, rows, message FROM portal_runs "
                         "WHERE 1=1" + frag + " ORDER BY id", params):
        runs[(r["supplier"], r["entity"])] = dict(r)
    cfgs = list_configs()
    con.close()
    out = []
    for c in cfgs:
        ents = sorted({e for (s, e) in creds if s == c["supplier"]}) or [None]
        for ent in ents:
            out.append({"supplier": c["supplier"], "kind": c["kind"], "enabled": c["enabled"],
                        "interval_hours": c.get("interval_hours") or 0,
                        "entity": ent, "has_creds": (c["supplier"], ent) in creds,
                        "last_run": runs.get((c["supplier"], ent))})
    return out


# ----------------------------------------------------- scheduled-pull eligibility
def _parse_finished(s):
    """Parse a portal_runs.finished timestamp (SQLite CURRENT_TIMESTAMP writes
    '%Y-%m-%d %H:%M:%S' in UTC) into a naive UTC datetime, tolerating a fractional
    second / trailing 'Z'. Returns None if unparseable — the caller treats an
    unparseable last-run as 'due' (fail-open toward pulling, never crash)."""
    if not s:
        return None
    s = str(s).strip().rstrip("Z").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def due_portal_fetches(now=None):
    """READ-ONLY: the list of (supplier, entity) portals DUE for a scheduled pull.

    A portal is due when ALL hold:
      • the config is enabled (enabled=1), AND
      • interval_hours > 0 (0 = scheduling OFF — the default), AND
      • stored credentials exist for that (supplier, entity), AND
      • either no SUCCESSFUL run exists yet, OR the latest successful run's `finished`
        is older than interval_hours before `now`.

    `now` (naive UTC datetime) is injectable for deterministic tests; defaults to
    utcnow(). NEVER raises — any failure logs a warning and returns [] (a scheduler
    fault must not crash the leader loop). This function makes NO outbound call and
    enqueues nothing; the caller (app._scrape_tick) is the only thing that, gated on a
    global kill-switch, enqueues a fetch per returned portal."""
    # P2 tenant isolation DEFERRED here: this runs on the SYSTEM scheduler tier, which
    # must enumerate EVERY tenant's due fetches and stamp each ENQUEUED job with that
    # row's tenant — that needs the worker-context pattern + caller (enqueuer) changes,
    # out of scope for this user-facing-CRUD slice. Left unscoped deliberately.
    try:
        now = now or datetime.datetime.utcnow()
        con = connect()
        try:
            cfgs = con.execute(
                "SELECT supplier, enabled, interval_hours FROM portal_configs").fetchall()
            creds = {(r["supplier"], r["entity"]) for r in
                     con.execute("SELECT supplier, entity FROM portal_credentials")}
            # latest SUCCESSFUL run's finished time per (supplier, entity)
            last_ok = {}
            for r in con.execute(
                    "SELECT supplier, entity, finished FROM portal_runs "
                    "WHERE status='ok' AND finished IS NOT NULL ORDER BY id"):
                last_ok[(r["supplier"], r["entity"])] = r["finished"]
        finally:
            con.close()
        out = []
        for c in cfgs:
            if not c["enabled"]:
                continue
            try:
                iv = float(c["interval_hours"] or 0)
            except (TypeError, ValueError):
                iv = 0.0
            if iv <= 0:
                continue
            sup = c["supplier"]
            # one scheduled pull per entity that has stored credentials for this portal
            for (s, ent) in creds:
                if s != sup:
                    continue
                fin = _parse_finished(last_ok.get((sup, ent)))
                if fin is None:
                    out.append((sup, ent))      # never run (or unparseable) -> due
                    continue
                age_h = (now - fin).total_seconds() / 3600.0
                if age_h >= iv:
                    out.append((sup, ent))
        return out
    except Exception as e:
        log.warning("due_portal_fetches failed (returning none): %s", e)
        return []


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


# ====================================================================================
# DOCUMENT-YIELDING ADAPTERS — pull statement FILES (not benchmark prices).
#
# The HttpJson/Csv/Demo adapters above return PRICE rows that scrape() loads into the
# my_prices benchmark. A DocumentAdapter instead DOWNLOADS the supplier's statement
# files (CSV/XLSX/XML/PDF) so they enter the SAME extract->review-draft->register
# pipeline an upload uses — that is the "onboard a portal with CONFIG, not code" goal.
# Such an adapter sets DOCUMENT_YIELDING=True; the orchestrator routes it to
# scrape_documents() (which enqueues each file on the intake queue) instead of the
# price-loading scrape() body.
# ====================================================================================
class DocumentAdapter(PortalAdapter):
    """Base for adapters that DOWNLOAD statement files. fetch_documents() returns a list
    of (filename, bytes); each is handed to the existing extract/register pipeline. A
    `log_step(step, detail)` callback records per-step progress on the portal_runs row so
    a failure names the step that failed (clearer breaker reasons)."""
    DOCUMENT_YIELDING = True

    def fetch_documents(self, creds, cfg, date_from, date_to, log_step):
        raise NotImplementedError

    # DocumentAdapters do not produce price rows; fetch() is never called for them.
    def fetch(self, creds, cfg, date_from, date_to):
        raise RuntimeError("document adapter: use fetch_documents()")


# A swappable HTTP session factory so tests inject a FAKE transport (no network). The
# real factory returns a requests.Session; a test monkeypatches this to return a stub
# exposing the same request()/get()/post() surface the adapter uses.
def _http_session():
    import requests
    return requests.Session()


_FORMAT_EXT = {"csv": ".csv", "xlsx": ".xlsx", "xls": ".xls",
               "xml": ".xml", "pdf": ".pdf", "zip": ".zip"}


def _filename_for(cfg, idx, url, fallback_format):
    """Best filename for a downloaded statement: a name in the URL's last path segment
    if it carries a known extension, else <supplier-ish>_<idx><ext-for-format>. The
    extension drives which pipeline branch extract.extract() takes, so we always end on
    a known extension."""
    import os as _os
    from urllib.parse import urlparse
    seg = _os.path.basename(urlparse(url or "").path) if url else ""
    if seg and "." in seg:
        ext = "." + seg.rsplit(".", 1)[1].lower()
        if ext in _FORMAT_EXT.values():
            return seg
    ext = _FORMAT_EXT.get((fallback_format or "").lower(), ".bin")
    return f"statement_{idx}{ext}"


class HttpFormAdapter(DocumentAdapter):
    """CONFIG-DRIVEN generic "form-login + download" adapter — the workhorse for the
    majority of low-IT supplier portals (POST a login form, GET a statement list, download
    each statement). A NEW portal is onboarded by FILLING cfg['config'], not writing code.

    cfg['config'] schema (all optional unless noted; base_url is prepended to every
    relative URL):

      AUTH (auth_mode, default 'form'):
        'form'   : POST login_url with the credentials + static fields.
            login_url        (required)            login form action (relative or absolute)
            login_method     'POST'|'GET'          default 'POST'
            username_field   form field NAME for the username (default 'username')
            password_field   form field NAME for the password (default 'password')
            login_extra      {field: value}        static hidden fields (csrf seed, etc.)
            login_as_json    bool                  send JSON body instead of form-encoded
        'basic'  : HTTP Basic auth (username/secret) on every request; no login POST.
        'oauth2_client_credentials':
            token_url        (required)            OAuth2 token endpoint
            token_field_map  {client_id, client_secret} -> 'username'|'secret'|literal
                             (default {client_id:'username', client_secret:'secret'})
            token_extra      {field: value}        extra token params (scope, audience…)
            token_path       dotted path to the access token in the JSON (default
                             'access_token'); sent as 'Authorization: Bearer <tok>'.

      SUCCESS CHECK (login_success, optional — verifies auth before enumerating):
            status           int|[ints]            acceptable status code(s) (default 2xx)
            redirect_contains substring required in the final URL after redirects
            text_contains    substring required in the login response body
            text_absent      substring that must NOT appear (e.g. 'login failed')

      ENUMERATE the downloadable statements (one of):
            list_url         the statement-listing page/endpoint URL
            list_method      'GET'|'POST'          default 'GET'
            list_params      {.. '_from','_to' placeholders substituted with the window}
          A) link_regex      regex over the listing TEXT whose group(1) is each download
                             URL (or path). Use for an HTML listing.
            link_limit       cap the number of matched links (default 200)
          B) url_template    a download-URL template enumerated over `ids` OR over the
                             window; '{id}','{from}','{to}' are substituted.
            ids              [list of ids] for the template (one download per id)
          PAGINATION (optional, applies to link_regex enumeration):
            page_param       query param name for the page number
            page_start       first page number (default 1)
            max_pages        hard cap on pages walked (default 1 = no pagination)
            next_link_regex  regex whose group(1) is the next-page URL (alternative to
                             page_param; walking stops when it stops matching)

      DOWNLOAD:
            download_method  'GET'|'POST'          default 'GET'
            format           csv|xlsx|xml|pdf|zip  the expected file format (drives the
                             stored filename extension -> the extract pipeline branch).

    SECURITY: credentials arrive already-decrypted from get_credentials (envelope-opened);
    they are sent to the portal but NEVER logged (log_step records steps/URLs/counts only).
    Runs ONLY on the worker tier via scrape_documents()."""
    DOCUMENT_YIELDING = True

    def fetch_documents(self, creds, cfg, date_from, date_to, log_step):
        from urllib.parse import urljoin
        c = cfg.get("config") or {}
        base = cfg.get("base_url") or ""
        username = (creds or {}).get("username") or ""
        secret = (creds or {}).get("secret") or ""

        def U(u):
            """Resolve a possibly-relative URL against base_url."""
            if not u:
                return u
            return u if u.startswith(("http://", "https://")) else urljoin(base + "/", u.lstrip("/"))

        def sub_window(s):
            return (str(s).replace("{from}", date_from or "").replace("{to}", date_to or "")
                    if s is not None else s)

        s = _http_session()
        auth_mode = (c.get("auth_mode") or "form").lower()
        req_auth = None
        headers = {}

        # ---- AUTH -----------------------------------------------------------------
        if auth_mode == "basic":
            req_auth = (username, secret)
            log_step("auth", "http-basic")
        elif auth_mode == "oauth2_client_credentials":
            if not c.get("token_url"):
                raise RuntimeError("http_form oauth2: token_url is required")
            fmap = c.get("token_field_map") or {"client_id": "username", "client_secret": "secret"}
            body = {}
            for field, src in fmap.items():
                body[field] = {"username": username, "secret": secret}.get(src, src)
            body.setdefault("grant_type", "client_credentials")
            for k, v in (c.get("token_extra") or {}).items():
                body[k] = v
            try:
                tr = s.request("POST", U(c["token_url"]), data=body, timeout=DEFAULT_TIMEOUT)
                tr.raise_for_status()
                tok = _dig(tr.json(), c.get("token_path") or "access_token")
            except Exception as e:
                raise RuntimeError(f"oauth2 token request failed: {type(e).__name__}: {e}")
            if not tok:
                raise RuntimeError("oauth2 token request returned no access token")
            headers["Authorization"] = f"Bearer {tok}"
            log_step("auth", "oauth2 client-credentials token acquired")
        elif auth_mode == "form":
            if not c.get("login_url"):
                raise RuntimeError("http_form: login_url is required for auth_mode 'form'")
            fields = dict(c.get("login_extra") or {})
            fields[c.get("username_field", "username")] = username
            fields[c.get("password_field", "password")] = secret
            kw = {"json": fields} if c.get("login_as_json") else {"data": fields}
            try:
                lr = s.request(c.get("login_method", "POST"), U(c["login_url"]),
                               timeout=DEFAULT_TIMEOUT, **kw)
            except Exception as e:
                raise RuntimeError(f"login request failed: {type(e).__name__}: {e}")
            self._check_login(lr, c.get("login_success") or {})
            log_step("auth", "form login OK")
        else:
            raise RuntimeError(f"http_form: unknown auth_mode '{auth_mode}'")

        # ---- ENUMERATE ------------------------------------------------------------
        download_urls = []
        if c.get("url_template"):
            tpl = c["url_template"]
            ids = c.get("ids")
            if ids:
                download_urls = [sub_window(tpl).replace("{id}", str(i)) for i in ids]
            else:
                download_urls = [sub_window(tpl)]
            log_step("enumerate", f"url_template -> {len(download_urls)} statement(s)")
        elif c.get("link_regex"):
            download_urls = self._enumerate_links(s, c, U, sub_window, req_auth, headers, log_step)
        else:
            raise RuntimeError("http_form: enumeration needs either url_template or link_regex")

        if not download_urls:
            log_step("enumerate", "no statements found in the window")
            return []

        # ---- DOWNLOAD -------------------------------------------------------------
        fmt = (c.get("format") or "csv").lower()
        method = c.get("download_method", "GET")
        out = []
        for idx, du in enumerate(download_urls, 1):
            try:
                r = s.request(method, U(du), auth=req_auth, headers=headers,
                              timeout=DEFAULT_TIMEOUT)
                r.raise_for_status()
            except Exception as e:
                raise RuntimeError(f"download {idx}/{len(download_urls)} failed: "
                                   f"{type(e).__name__}: {e}")
            data = r.content
            if not data:
                log_step("download", f"statement {idx} was empty — skipped")
                continue
            out.append((_filename_for(cfg, idx, du, fmt), data))
        log_step("download", f"downloaded {len(out)} statement file(s)")
        return out

    def _check_login(self, resp, spec):
        """Verify the login response against the configured success check; raise a clear
        per-criterion error on a wrong credential / failed login so the run records a
        failed status and the breaker can trip."""
        want = spec.get("status")
        if want is not None:
            ok = (resp.status_code in want) if isinstance(want, (list, tuple)) else (resp.status_code == want)
            if not ok:
                raise RuntimeError(f"login status {resp.status_code} (wanted {want})")
        elif resp.status_code >= 400:
            raise RuntimeError(f"login failed: HTTP {resp.status_code}")
        if spec.get("redirect_contains"):
            url = getattr(resp, "url", "") or ""
            if spec["redirect_contains"] not in url:
                raise RuntimeError("login did not redirect as expected (auth likely failed)")
        body = None
        if spec.get("text_contains") or spec.get("text_absent"):
            body = resp.text or ""
        if spec.get("text_contains") and spec["text_contains"] not in body:
            raise RuntimeError("login success marker absent (auth likely failed)")
        if spec.get("text_absent") and spec["text_absent"] in body:
            raise RuntimeError(f"login failure marker present: {spec['text_absent']!r}")

    def _enumerate_links(self, s, c, U, sub_window, req_auth, headers, log_step):
        """Walk the statement-listing page(s) and collect download URLs by regex. Supports
        pagination via either a page_param counter or a next_link_regex chain, capped by
        max_pages (default 1)."""
        import re as _re
        if not c.get("list_url"):
            raise RuntimeError("http_form: link_regex enumeration needs list_url")
        pat = _re.compile(c["link_regex"])
        # window placeholders ({from}/{to}) in list_params string values are substituted
        params = {k: (sub_window(v) if isinstance(v, str) else v)
                  for k, v in (c.get("list_params") or {}).items()}
        method = c.get("list_method", "GET")
        max_pages = max(1, _as_int_local(c.get("max_pages"), 1))
        page_param = c.get("page_param")
        page = _as_int_local(c.get("page_start"), 1)
        next_re = _re.compile(c["next_link_regex"]) if c.get("next_link_regex") else None
        limit = _as_int_local(c.get("link_limit"), 200)
        urls, next_url, pages = [], U(c["list_url"]), 0
        while next_url and pages < max_pages:
            p = dict(params)
            if page_param:
                p[page_param] = page
            try:
                lr = s.request(method, next_url, params=p, auth=req_auth,
                               headers=headers, timeout=DEFAULT_TIMEOUT)
                lr.raise_for_status()
            except Exception as e:
                raise RuntimeError(f"statement-list page {pages+1} failed: "
                                   f"{type(e).__name__}: {e}")
            text = lr.text or ""
            found = pat.findall(text)
            for m in found:
                if len(urls) >= limit:
                    break
                urls.append(m if isinstance(m, str) else m[0])
            pages += 1
            if next_re:
                nm = next_re.search(text)
                next_url = U(nm.group(1)) if nm else None
            elif page_param:
                page += 1
                next_url = U(c["list_url"]) if pages < max_pages else None
            else:
                next_url = None
        log_step("enumerate", f"link_regex matched {len(urls)} statement(s) over {pages} page(s)")
        return urls


def _as_int_local(v, default):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


_BUILTIN = {"http_json": HttpJsonAdapter, "csv": CsvExportAdapter, "demo": DemoAdapter,
            "http_form": HttpFormAdapter}

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
    """Scrape one portal account. A PRICE adapter (http_json/csv/demo/custom) loads the
    MY-Prices benchmark (source 'portal:<SUPPLIER>'). A DOCUMENT adapter (http_form, or a
    registered DocumentAdapter) downloads STATEMENT FILES and enqueues them into the SAME
    extract->review-draft->register pipeline an upload uses (scrape_documents). Returns a
    summary dict; records the run; raises RuntimeError on misconfiguration or an upstream
    failure (recorded on the run first). Called only on the worker tier via _do_fetch."""
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
    if getattr(adapter, "DOCUMENT_YIELDING", False):
        return scrape_documents(supplier, entity, cfg, creds, adapter, date_from, date_to)
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


def scrape_documents(supplier, entity, cfg, creds, adapter, date_from=None, date_to=None):
    """Download statement FILES from a portal and ENQUEUE each into the existing intake
    queue (waiting_room.enqueue), so they flow through the SAME extract->review-draft->
    register pipeline as an upload — deterministic-first, human-confirmed (advisory until
    confirmed; autopilot may auto-file when ON, exactly as for an upload). The enqueue
    `backend` is the SUPPLIER, so the supplier's parser/AI routing is unchanged.

    Records a portal_runs row with a STEP-by-step message so a failure names the failing
    step (clearer breaker reasons). NEVER loads my_prices (that's the price-scrape path).
    Returns {supplier, entity, fetched, loaded} where `fetched` = files downloaded and
    `loaded` = jobs enqueued (re-using scrape()'s return shape so _do_fetch logs uniformly).
    Raises (after recording 'failed') on any per-step failure so the worker retries/breaks."""
    supplier = supplier.upper()
    con = connect()
    run_id = _start_run(con, supplier, entity)
    steps = []

    def log_step(step, detail):
        # progress only — NEVER a secret. Kept short; the latest steps form the run message.
        steps.append(f"{step}: {detail}")
        log.info("portal[%s/%s] %s: %s", supplier, entity, step, detail)

    try:
        docs = adapter.fetch_documents(creds or {}, cfg, date_from, date_to, log_step)
        import waiting_room as IQ
        user = (creds or {}).get("user") or "portal-fetch"
        enqueued = 0
        for fname, data in docs:
            try:
                IQ.enqueue(data, fname, backend=supplier, user=f"portal:{supplier}")
                enqueued += 1
            except ValueError as e:           # empty upload etc. — skip, keep going
                log_step("enqueue", f"skipped {fname}: {e}")
        msg = f"{enqueued} statement(s) enqueued from {len(docs)} downloaded · " + " | ".join(steps[-4:])
        _finish_run(con, run_id, "ok", enqueued, msg)
        con.close()
        return {"supplier": supplier, "entity": entity,
                "fetched": len(docs), "loaded": enqueued}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if steps:
            msg += " · steps: " + " | ".join(steps[-4:])
        _finish_run(con, run_id, "failed", 0, msg)
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
