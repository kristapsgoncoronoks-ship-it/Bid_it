"""
INTAKE QUEUE — a durable "waiting room" for uploaded invoice batches.

WHY: extracting a PDF/ZIP batch can be heavy (it may call an AI backend). Doing it
inline on every upload lets a burst of uploads overload the server. Instead the
upload is accepted instantly and parked here; the heavy work runs later in a
bounded worker that processes ONE job at a time, so load stays flat no matter how
many files arrive at once.

NO DATA LOSS — the precautions:
  • the uploaded bytes are written to disk and fsync'd (file + directory) BEFORE
    the queue row is committed, so a crash at any instant leaves the job either
    fully enqueued or not at all — never a half-written file with no record.
  • the original bytes are KEPT in inbox/ until the job is marked `done` (i.e. a
    human has reviewed and committed the draft), so nothing is discarded early.
  • processing is at-least-once: a crash mid-extract leaves a `processing` row
    whose lease expires and is reclaimed and retried. This is safe because
    extraction only produces a DRAFT — no business data is written until a person
    confirms it in the existing review screen — so re-processing can never
    double-post anything.
  • identical re-uploads dedupe by content hash; transient failures retry with
    capped backoff, then land in `failed` (kept, never deleted) for inspection.

Lifecycle:  queued --(claim+lease)--> processing --> ready --(human confirms)--> done
                                          └─ on error: queued (retry) or failed

Storage:    inbox/<sha256>.bin     the original upload bytes (kept until `done`)
            intake.db              the queue itself

This is an operational queue, not master data: who/when is recorded on each row
(uploaded_by/uploaded_at) but the churny status transitions are not audit-logged,
to keep the table lean. The authoritative audit happens at confirm time in the
existing statement-registration path.

CLI:
    python waiting_room.py --work      drain forever (run as a separate worker)
    python waiting_room.py --once      drain the current backlog and exit
    python waiting_room.py --status    print counts by state
"""
import os, sqlite3, hashlib, json, time, datetime
import applog
import tenancy

WORKDIR = os.path.dirname(os.path.abspath(__file__))
log = applog.get("waiting_room")
DB = os.environ.get("INTAKE_DB", f"{WORKDIR}/intake.db")
INBOX = os.environ.get("INTAKE_INBOX", f"{WORKDIR}/inbox")

LEASE_SECONDS = 600          # a claimed job is "owned" for this long before reclaim
MAX_ATTEMPTS = 5             # give up (-> failed) after this many HARD tries
BACKOFF_BASE = 30            # seconds; doubles each retry up to BACKOFF_MAX
BACKOFF_MAX = 1800
POLL_SECONDS = 5            # worker idle poll interval
# When the AI backend is out of tokens/quota or rate-limited, the job is parked in
# 'waiting' and retried on this cadence — every few hours — WITHOUT counting toward
# MAX_ATTEMPTS (a quota outage is not the document's fault). Default 4 hours;
# override with INTAKE_RETRY_AFTER_TOKENS.
RETRY_AFTER_TOKENS = int(os.environ.get("INTAKE_RETRY_AFTER_TOKENS", 4 * 3600))
# ...but don't retry forever: after this many 4-hour attempts the quota is clearly
# not coming back on its own, so STOP auto-processing and put the job in 'held' —
# it stays safely in the waiting room until a user presses "Send now" to retry it
# manually. Default 6 (≈24h of trying); override with INTAKE_MAX_TOKEN_RETRIES.
MAX_TOKEN_RETRIES = int(os.environ.get("INTAKE_MAX_TOKEN_RETRIES", 6))

# ---- per-supplier rate-limit / concurrency / backoff / circuit-breaker --------
# Phase-0 safety substrate for the automated-capture flagship. OPT-IN by design: a
# supplier with NO row in supplier_rate_limits is UNGOVERNED and behaves exactly as
# today (unlimited) — the existing extract/register/close paths are untouched unless
# a limit is explicitly configured. When a limit row OMITS a field, these defaults
# apply so a partial config is still sane.
DEFAULT_MAX_CONCURRENT = 2          # in-flight (status='processing') jobs per supplier
DEFAULT_MIN_INTERVAL_S = 5.0        # minimum spacing between two claims for a supplier
DEFAULT_BREAKER_THRESHOLD = 5       # consecutive failures that trip the breaker
DEFAULT_BREAKER_COOLDOWN_S = 300.0  # how long the breaker stays open once tripped

_SCHEMA_READY = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS intake_jobs (
    id INTEGER PRIMARY KEY,
    sha256 TEXT,
    filename TEXT,
    size INTEGER,
    backend TEXT,
    period TEXT,
    uploaded_by TEXT,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'queued',          -- queued|processing|ready|failed|done|waiting|held
    attempts INTEGER DEFAULT 0,
    defer_count INTEGER DEFAULT 0,         -- consecutive token/quota deferrals
    lease_until TEXT,
    next_attempt_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    stored_path TEXT,                       -- inbox filename (resolved under INBOX)
    draft TEXT,                             -- JSON draft once ready (no binary)
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_intake_status ON intake_jobs(status, next_attempt_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_intake_sha ON intake_jobs(sha256);
"""


def _now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _at(epoch):
    return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def _drop_tenant(d):
    """Strip the P1 multi-tenancy plumbing column from a row dict before it is
    surfaced as a caller contract (job rows, supplier-limit rows). The tenant_id
    column is pure schema plumbing (no query reads it yet); excluding it here keeps
    the exposed dict shape byte-identical to before the column was added."""
    d.pop(tenancy.TENANT_COLUMN, None)
    return d


def connect():
    import db_tuning, db_migrate
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    # a queue wants durability and cross-process concurrency: WAL lets the web UI
    # read progress while a worker writes, and busy_timeout lets several worker
    # processes claim jobs without tripping "database is locked".
    db_tuning.tune(con)
    # APPEND-ONLY migration list (positions are stable; never reorder/delete).
    _MIGR = [
        "ALTER TABLE intake_jobs ADD COLUMN defer_count INTEGER DEFAULT 0",
        # D4: a job 'kind' the worker dispatches on, plus a JSON payload for jobs
        # that carry data rather than inbox bytes (e.g. statement registration).
        "ALTER TABLE intake_jobs ADD COLUMN kind TEXT DEFAULT 'extract'",
        "ALTER TABLE intake_jobs ADD COLUMN payload TEXT",
        # reliability telemetry: a sampled DLQ-size history for growth-rate alerting
        # (queue_health / dlq_growth). Self-contained in intake.db; pruned to ~7 days.
        "CREATE TABLE IF NOT EXISTS intake_health_samples (ts TEXT, dlq INTEGER)",
        # per-supplier rate-limit / concurrency / breaker CONFIG (opt-in). A row here
        # = this supplier (= a job's `backend`) is GOVERNED; absence = unlimited. The
        # later scraper/fetch path consumes this; nothing today writes rows by default.
        "CREATE TABLE IF NOT EXISTS supplier_rate_limits ("
        " supplier TEXT PRIMARY KEY, max_concurrent INTEGER, min_interval_s REAL,"
        " breaker_threshold INTEGER, breaker_cooldown_s REAL, enabled INTEGER DEFAULT 1)",
        # per-supplier limiter STATE (live counters the eligibility math reads/writes).
        "CREATE TABLE IF NOT EXISTS supplier_rate_state ("
        " supplier TEXT PRIMARY KEY, last_start_at TEXT, consec_failures INTEGER DEFAULT 0,"
        " breaker_open_until TEXT)",
        # P1 multi-tenancy (schema plumbing only): stamp the intake queue + the per-
        # supplier rate-limit config/state tables with a tenant_id; existing rows
        # backfill to DEFAULT_TENANT_ID via the column DEFAULT, new rows default too.
        # A per-supplier rate-limit IS tenant-scoped once multi-client, so the two
        # limiter tables get the column alongside intake_jobs. The DLQ-size health
        # samples (intake_health_samples) are GLOBAL queue telemetry, not tenant rows,
        # so they are deliberately left unstamped. NO query reads this column yet (the
        # `multitenant` switch is OFF and scope_clause is unwired until P2): _claim, the
        # rate-limiter eligibility math and the worker dispatch all select/filter named
        # columns, never tenant_id, so this is a pure no-behavior-change addition. TEXT
        # is audit-safe. APPEND-ONLY — keep at END (positions are stable).
        *tenancy.tenant_column_ddls([
            "intake_jobs", "supplier_rate_limits", "supplier_rate_state",
        ]),
    ]
    if DB != ":memory:" and DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        db_migrate.apply(con, "waiting_room", _MIGR)
        _SCHEMA_READY.add(DB)
    elif DB == ":memory:":
        con.executescript(SCHEMA)
        db_migrate.apply(con, "waiting_room", _MIGR)
    return con


# job kinds the worker dispatches on (the 'kind' column). EXTRACTION is the legacy
# default (file bytes -> draft); REGISTRATION carries a validated statement payload
# that the engine writes into suppliers.db off the web request (decoupling D4).
KIND_EXTRACT = "extract"
KIND_REGISTER = "register"
# A one-click MONTHLY CLOSE job (decoupling D5's web surface): carries NO inbox bytes,
# just a period. The web request ENQUEUES this; the worker calls engine_close.close so
# the request never runs the close inline nor holds a writable engine-owned product-DB
# handle. engine_close's own "close-run" process_lock serialises actual execution.
KIND_CLOSE = "close"
# A supplier-portal FETCH job (automated-capture flagship): carries NO inbox bytes, just
# the supplier/entity/date-window to pull. The web request ENQUEUES this; the worker calls
# portal_scraper.scrape OFF the request so a fetch never runs inline in a web request. The
# job's `backend` is the SUPPLIER, so the per-supplier rate-limiter/breaker governs CLAIM
# eligibility and the worker records each outcome via record_outcome to drive the breaker.
KIND_FETCH = "fetch"


# ---------------------------------------------------------------- inbox files
def _safe_inbox(name):
    """Resolve an inbox filename and ensure it stays inside INBOX."""
    base = os.path.realpath(INBOX)
    real = os.path.realpath(os.path.join(INBOX, os.path.basename(name)))
    if not (real == base or real.startswith(base + os.sep)):
        raise ValueError(f"inbox path escapes store: {name!r}")
    return real

def _write_inbox(sha, data):
    """Write bytes durably: temp file -> fsync -> atomic rename -> fsync dir.
    Returns the bare filename stored on the job row."""
    os.makedirs(INBOX, exist_ok=True)
    fname = f"{sha}.bin"
    final = _safe_inbox(fname)
    tmp = final + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, final)
    try:                                    # fsync the directory entry (POSIX)
        dfd = os.open(INBOX, os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    except (OSError, AttributeError) as e:
        log.debug("inbox dir fsync not supported (best effort): %s", e)  # e.g. Windows
    return fname

def read_bytes(stored_path):
    return open(_safe_inbox(stored_path), "rb").read()


# ---------------------------------------------------------------- enqueue
def enqueue(data, filename, backend=None, period=None, user="system"):
    """Accept an upload into the waiting room. Durable: bytes hit disk (fsync'd)
    before the row is committed. Idempotent by content hash — re-uploading the
    same bytes returns the existing job (re-queuing it if it had failed).
    Returns (job_id, status)."""
    if not data:
        raise ValueError("empty upload")
    sha = hashlib.sha256(data).hexdigest()
    con = connect()
    existing = con.execute("SELECT id, status FROM intake_jobs WHERE sha256=?", (sha,)).fetchone()
    if existing:
        # same file already known — re-queue it only if it had failed/done, else
        # leave the in-flight/ready job as-is (idempotent upload).
        if existing["status"] in ("failed", "done"):
            con.execute("""UPDATE intake_jobs SET status='queued', attempts=0,
                           lease_until=NULL, next_attempt_at=NULL, error=NULL,
                           started_at=NULL, finished_at=NULL, draft=NULL,
                           uploaded_by=?, uploaded_at=? WHERE id=?""",
                        (user, _now(), existing["id"]))
            con.commit()
        con.close()
        return existing["id"], "queued" if existing["status"] in ("failed", "done") else existing["status"]
    stored = _write_inbox(sha, data)        # bytes are durably on disk FIRST
    cur = con.execute("""INSERT INTO intake_jobs
        (sha256, filename, size, backend, period, uploaded_by, stored_path, status, kind)
        VALUES (?,?,?,?,?,?,?, 'queued', ?)""",
        (sha, filename, len(data), backend, period, user, stored, KIND_EXTRACT))
    con.commit()                            # only now is the job visible/durable
    jid = cur.lastrowid
    con.close()
    return jid, "queued"


def enqueue_registration(payload, user="system"):
    """Enqueue a supplier-statement REGISTRATION job (decoupling D4). `payload` is
    the already-VALIDATED statement (supplier, statement_ref, period, statement_date,
    lines, customer, notes, draft id) — the web request has done the synchronous
    validation gate and PDF vaulting; only the suppliers.db WRITE is deferred here so
    the request holds no writable suppliers.db handle. The engine worker dispatches
    on kind='register' and calls invoice_control.register_statement (at-least-once;
    that write is idempotent on its UNIQUE keys). Returns (job_id, status).

    Carries NO inbox bytes — the payload IS the job. We still set a deterministic
    sha256 over (supplier, statement_ref) so the UNIQUE index gives natural
    de-duplication / requeue on a re-confirm of the same statement."""
    supplier = (payload.get("supplier") or "").strip()
    statement_ref = (payload.get("statement_ref") or "").strip()
    if not supplier or not statement_ref:
        raise ValueError("registration payload needs supplier + statement_ref")
    sha = hashlib.sha256(f"register:{supplier}:{statement_ref}".encode()).hexdigest()
    body = json.dumps(payload)
    con = connect()
    existing = con.execute("SELECT id, status FROM intake_jobs WHERE sha256=?", (sha,)).fetchone()
    if existing:
        # a re-confirm of the same statement: refresh the payload and re-queue it
        # (registration is idempotent, so re-running is safe).
        con.execute("""UPDATE intake_jobs SET status='queued', attempts=0, defer_count=0,
                       lease_until=NULL, next_attempt_at=NULL, error=NULL,
                       started_at=NULL, finished_at=NULL, payload=?,
                       uploaded_by=?, uploaded_at=? WHERE id=?""",
                    (body, user, _now(), existing["id"]))
        con.commit(); con.close()
        return existing["id"], "queued"
    cur = con.execute("""INSERT INTO intake_jobs
        (sha256, filename, size, backend, period, uploaded_by, status, kind, payload)
        VALUES (?,?,?,?,?,?, 'queued', ?, ?)""",
        (sha, f"{supplier} {statement_ref}", 0, supplier, payload.get("period"),
         user, KIND_REGISTER, body))
    con.commit()
    jid = cur.lastrowid
    con.close()
    return jid, "queued"


def enqueue_close(period, user="system"):
    """Enqueue a one-click MONTHLY CLOSE job (decoupling D5's web surface). The web
    request does NO close work itself — it only parks this fileless job; the engine
    worker dispatches on kind='close' and calls engine_close.close(period) OFF the web
    request, so the request holds no writable engine-owned product-DB handle. Carries
    NO inbox bytes — the period IS the job. Returns (job_id, status).

    A close is RE-RUNNABLE (every stage is idempotent), so we dedup on a deterministic
    sha over the period: an existing row for the same period is refreshed and re-queued
    (exactly like enqueue_registration's re-confirm branch) rather than duplicated.
    Two queued close jobs are still safe — engine_close's 'close-run' process_lock
    serialises the actual execution and a contending second run re-queues to retry."""
    period = (period or "").strip()
    if not period:
        raise ValueError("monthly close needs a period")
    sha = hashlib.sha256(f"close:{period}".encode()).hexdigest()
    body = json.dumps({"period": period})
    con = connect()
    existing = con.execute("SELECT id, status FROM intake_jobs WHERE sha256=?", (sha,)).fetchone()
    if existing:
        # re-running a close for the same period: refresh the payload and re-queue it
        # (idempotent, so re-running is safe).
        con.execute("""UPDATE intake_jobs SET status='queued', attempts=0, defer_count=0,
                       lease_until=NULL, next_attempt_at=NULL, error=NULL,
                       started_at=NULL, finished_at=NULL, payload=?,
                       uploaded_by=?, uploaded_at=? WHERE id=?""",
                    (body, user, _now(), existing["id"]))
        con.commit(); con.close()
        return existing["id"], "queued"
    cur = con.execute("""INSERT INTO intake_jobs
        (sha256, filename, size, backend, period, uploaded_by, status, kind, payload)
        VALUES (?,?,?,?,?,?, 'queued', ?, ?)""",
        (sha, f"monthly close {period}", 0, None, period,
         user, KIND_CLOSE, body))
    con.commit()
    jid = cur.lastrowid
    con.close()
    return jid, "queued"


def enqueue_fetch(supplier, entity, date_from=None, date_to=None, user="system"):
    """Enqueue a supplier-portal FETCH job (automated-capture flagship). The web request
    does NO fetching itself — it only parks this fileless job; the engine worker dispatches
    on kind='fetch' and calls portal_scraper.scrape OFF the web request, so a fetch never
    runs inline in a web request and the per-supplier rate-limiter/breaker governs it.

    `backend` is set to the SUPPLIER (uppercased, matching portal_scraper.scrape) so the
    limiter (which keys on `backend`) governs CLAIM eligibility. Carries NO inbox bytes —
    the (supplier, entity, dates) ARE the job. Returns (job_id, status).

    A fetch is RE-RUNNABLE, so we dedup on a deterministic sha over the request
    (supplier+entity+window): an existing row is refreshed and re-queued (exactly like
    enqueue_registration/enqueue_close's re-confirm branch) rather than duplicated."""
    supplier = (supplier or "").strip()
    if not supplier:
        raise ValueError("portal fetch needs a supplier")
    supplier_up = supplier.upper()
    entity = (entity or "").strip()
    sha = hashlib.sha256(
        f"fetch:{supplier_up}:{entity}:{date_from}:{date_to}".encode()).hexdigest()
    body = json.dumps({"supplier": supplier, "entity": entity,
                       "date_from": date_from, "date_to": date_to})
    con = connect()
    existing = con.execute("SELECT id, status FROM intake_jobs WHERE sha256=?", (sha,)).fetchone()
    if existing:
        # a re-run of the same fetch: refresh the payload and re-queue it (idempotent).
        con.execute("""UPDATE intake_jobs SET status='queued', attempts=0, defer_count=0,
                       lease_until=NULL, next_attempt_at=NULL, error=NULL,
                       started_at=NULL, finished_at=NULL, payload=?,
                       uploaded_by=?, uploaded_at=? WHERE id=?""",
                    (body, user, _now(), existing["id"]))
        con.commit(); con.close()
        return existing["id"], "queued"
    cur = con.execute("""INSERT INTO intake_jobs
        (sha256, filename, size, backend, period, uploaded_by, status, kind, payload)
        VALUES (?,?,?,?,?,?, 'queued', ?, ?)""",
        (sha, f"fetch {supplier}/{entity}", 0, supplier_up, None,
         user, KIND_FETCH, body))
    con.commit()
    jid = cur.lastrowid
    con.close()
    return jid, "queued"


# ---------------------------------------------------------- rate-limit config API
def _as_int(v, default):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default

def _as_float(v, default):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default

def set_supplier_limit(supplier, max_concurrent=None, min_interval_s=None,
                       breaker_threshold=None, breaker_cooldown_s=None, enabled=True):
    """Upsert a per-supplier limit row (= GOVERN this supplier). A None field falls
    back to its DEFAULT_* constant so a partial config is still sane. `supplier` is the
    job's `backend` value the limiter keys on. Returns the resolved row as a dict."""
    supplier = (supplier or "").strip()
    if not supplier:
        raise ValueError("supplier required")
    mc = _as_int(max_concurrent, DEFAULT_MAX_CONCURRENT)
    mi = _as_float(min_interval_s, DEFAULT_MIN_INTERVAL_S)
    bt = _as_int(breaker_threshold, DEFAULT_BREAKER_THRESHOLD)
    bc = _as_float(breaker_cooldown_s, DEFAULT_BREAKER_COOLDOWN_S)
    en = 1 if enabled else 0
    con = connect()
    try:
        con.execute(
            """INSERT INTO supplier_rate_limits
               (supplier, max_concurrent, min_interval_s, breaker_threshold,
                breaker_cooldown_s, enabled) VALUES (?,?,?,?,?,?)
               ON CONFLICT(supplier) DO UPDATE SET
                 max_concurrent=excluded.max_concurrent,
                 min_interval_s=excluded.min_interval_s,
                 breaker_threshold=excluded.breaker_threshold,
                 breaker_cooldown_s=excluded.breaker_cooldown_s,
                 enabled=excluded.enabled""",
            (supplier, mc, mi, bt, bc, en))
        # ensure a state row exists so the eligibility join is straightforward
        con.execute("INSERT OR IGNORE INTO supplier_rate_state (supplier) VALUES (?)",
                    (supplier,))
        con.commit()
    finally:
        con.close()
    return {"supplier": supplier, "max_concurrent": mc, "min_interval_s": mi,
            "breaker_threshold": bt, "breaker_cooldown_s": bc, "enabled": en}

def get_supplier_limit(supplier):
    """Return the limit row for `supplier` as a dict, or None if ungoverned."""
    supplier = (supplier or "").strip()
    if not supplier:
        return None
    con = connect()
    try:
        r = con.execute("SELECT * FROM supplier_rate_limits WHERE supplier=?",
                        (supplier,)).fetchone()
    finally:
        con.close()
    # Exclude the P1 tenant_id plumbing from the surfaced limit-row contract (callers
    # consume this dict by key) — adding a multi-tenancy column must not change the
    # exposed shape.
    return _drop_tenant(dict(r)) if r else None

def clear_supplier_limit(supplier):
    """Delete the limit row (back to UNLIMITED). The state row is also dropped so a
    later re-govern starts clean. Returns True if a row was removed."""
    supplier = (supplier or "").strip()
    if not supplier:
        return False
    con = connect()
    try:
        cur = con.execute("DELETE FROM supplier_rate_limits WHERE supplier=?", (supplier,))
        con.execute("DELETE FROM supplier_rate_state WHERE supplier=?", (supplier,))
        con.commit()
        return (cur.rowcount or 0) > 0
    finally:
        con.close()

def list_supplier_limits():
    """All governed suppliers joined with their live state (for a future admin UI).
    Returns a list of dicts ordered by supplier."""
    con = connect()
    try:
        rows = con.execute(
            """SELECT l.supplier, l.max_concurrent, l.min_interval_s,
                      l.breaker_threshold, l.breaker_cooldown_s, l.enabled,
                      s.last_start_at, s.consec_failures, s.breaker_open_until
                 FROM supplier_rate_limits l
                 LEFT JOIN supplier_rate_state s ON s.supplier=l.supplier
                 ORDER BY l.supplier""").fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


# ------------------------------------------------- eligibility (blocked suppliers)
def _blocked_suppliers(con, now):
    """Return the set of GOVERNED suppliers currently BLOCKED from claiming a job.

    A governed supplier (an ENABLED supplier_rate_limits row) is blocked if ANY of:
      • breaker open:  breaker_open_until > now;
      • concurrency:   in-flight (status='processing') count for that backend >= max_concurrent;
      • min-interval:  last_start_at within min_interval_s of now.

    Only governed suppliers can ever be in this set, so an ungoverned job is NEVER
    affected. Computed inside the caller's BEGIN IMMEDIATE so it is consistent with the
    claim SELECT. NEVER raises: any failure degrades to "no blocking" (treat all as
    unlimited) rather than wedging the queue — a limiter fault must not stop intake."""
    try:
        limits = con.execute(
            "SELECT * FROM supplier_rate_limits WHERE enabled=1").fetchall()
        if not limits:
            return set()
        # in-flight processing count per backend (one scan, only governed keys matter)
        inflight = {}
        for r in con.execute(
                "SELECT backend, COUNT(*) n FROM intake_jobs "
                "WHERE status='processing' AND backend IS NOT NULL "
                "GROUP BY backend").fetchall():
            inflight[r["backend"]] = r["n"]
        state = {}
        for r in con.execute("SELECT * FROM supplier_rate_state").fetchall():
            state[r["supplier"]] = r
        now_epoch = time.time()
        blocked = set()
        for lim in limits:
            sup = lim["supplier"]
            st = state.get(sup)
            # breaker open?
            if st is not None and st["breaker_open_until"] and st["breaker_open_until"] > now:
                blocked.add(sup); continue
            # concurrency cap?
            mc = _as_int(lim["max_concurrent"], DEFAULT_MAX_CONCURRENT)
            if inflight.get(sup, 0) >= mc:
                blocked.add(sup); continue
            # min-interval spacing?
            mi = _as_float(lim["min_interval_s"], DEFAULT_MIN_INTERVAL_S)
            if st is not None and st["last_start_at"]:
                last = _parse_ts(st["last_start_at"])
                if last is not None:
                    elapsed = now_epoch - last.replace(
                        tzinfo=datetime.timezone.utc).timestamp()
                    if elapsed < mi:
                        blocked.add(sup); continue
        return blocked
    except Exception as e:
        log.warning("_blocked_suppliers failed (degrading to no blocking): %s", e)
        return set()


def _is_governed(con, supplier):
    """True if `supplier` has an ENABLED limit row (so its claim should stamp state)."""
    if not supplier:
        return False
    try:
        r = con.execute(
            "SELECT 1 FROM supplier_rate_limits WHERE supplier=? AND enabled=1",
            (supplier,)).fetchone()
        return r is not None
    except Exception as e:
        log.warning("_is_governed(%s) failed: %s", supplier, e)
        return False


# ---------------------------------------------------------------- claim + process
def _claim(con):
    """Atomically take the next eligible job (oldest ready-to-run, or a stale
    lease to reclaim). BEGIN IMMEDIATE serialises workers so a job is claimed
    once. Returns the pre-update row or None.

    Per-supplier rate limiting (OPT-IN): governed suppliers that are currently blocked
    (breaker open / concurrency cap / min-interval) are EXCLUDED from the claim so the
    next eligible UNblocked job surfaces — ungoverned suppliers are never in the blocked
    set, so they are untouched. When a governed supplier's job is claimed its
    last_start_at is stamped in the same transaction so the spacing math stays live."""
    now = _now()
    con.execute("BEGIN IMMEDIATE")
    # blocked-supplier set is read INSIDE the transaction so it is consistent with the
    # claim SELECT; it can only ever contain GOVERNED suppliers (opt-in).
    blocked = _blocked_suppliers(con, now)
    if blocked:
        ph = ",".join("?" * len(blocked))
        extra = f" AND (backend IS NULL OR backend NOT IN ({ph}))"
        params = (now, now, *blocked)
    else:
        extra = ""
        params = (now, now)
    row = con.execute(f"""SELECT * FROM intake_jobs WHERE (
          (status IN ('queued','waiting') AND (next_attempt_at IS NULL OR next_attempt_at<=?))
       OR (status='processing' AND lease_until IS NOT NULL AND lease_until<=?)
        ){extra}
        ORDER BY id LIMIT 1""", params).fetchone()
    if not row:
        con.execute("COMMIT")
        return None
    con.execute("""UPDATE intake_jobs SET status='processing', attempts=attempts+1,
                   lease_until=?, started_at=COALESCE(started_at,?), error=NULL
                   WHERE id=?""",
                (_at(time.time() + LEASE_SECONDS), now, row["id"]))
    # stamp the claim time for a governed supplier so min-interval/concurrency math is
    # live; ungoverned suppliers carry no state row and are untouched.
    if _is_governed(con, row["backend"]):
        con.execute(
            "INSERT INTO supplier_rate_state (supplier, last_start_at) VALUES (?, ?) "
            "ON CONFLICT(supplier) DO UPDATE SET last_start_at=excluded.last_start_at",
            (row["backend"], now))
    con.execute("COMMIT")
    return row


# ----------------------------------------------------------- breaker outcome API
def record_outcome(supplier, ok):
    """Record a fetch/scrape OUTCOME for a GOVERNED supplier (the later fetch path
    calls this). No-op for an ungoverned supplier. NEVER raises — a limiter fault must
    not break the caller.

    ok=True : reset consec_failures=0, clear breaker_open_until (recovery).
    ok=False: increment consec_failures; if it reaches breaker_threshold, OPEN the
              breaker (breaker_open_until = now + breaker_cooldown_s) and log a warning.
    """
    try:
        supplier = (supplier or "").strip()
        if not supplier:
            return
        con = connect()
        try:
            lim = con.execute(
                "SELECT * FROM supplier_rate_limits WHERE supplier=? AND enabled=1",
                (supplier,)).fetchone()
            if lim is None:
                return                      # ungoverned -> no-op
            con.execute("INSERT OR IGNORE INTO supplier_rate_state (supplier) VALUES (?)",
                        (supplier,))
            if ok:
                con.execute(
                    "UPDATE supplier_rate_state SET consec_failures=0, "
                    "breaker_open_until=NULL WHERE supplier=?", (supplier,))
            else:
                st = con.execute(
                    "SELECT consec_failures FROM supplier_rate_state WHERE supplier=?",
                    (supplier,)).fetchone()
                fails = _as_int(st["consec_failures"], 0) + 1 if st else 1
                thresh = _as_int(lim["breaker_threshold"], DEFAULT_BREAKER_THRESHOLD)
                if fails >= thresh:
                    cooldown = _as_float(lim["breaker_cooldown_s"],
                                         DEFAULT_BREAKER_COOLDOWN_S)
                    open_until = _at(time.time() + cooldown)
                    con.execute(
                        "UPDATE supplier_rate_state SET consec_failures=?, "
                        "breaker_open_until=? WHERE supplier=?",
                        (fails, open_until, supplier))
                    log.warning("circuit-breaker OPEN for supplier %s after %s "
                                "consecutive failures (cooldown %ss, until %s)",
                                supplier, fails, cooldown, open_until)
                else:
                    con.execute(
                        "UPDATE supplier_rate_state SET consec_failures=? "
                        "WHERE supplier=?", (fails, supplier))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        log.warning("record_outcome(%s, ok=%s) failed: %s", supplier, ok, e)


def breaker_state(supplier):
    """Read helper for surfacing later: {open: bool, open_until, consec_failures}.
    For an ungoverned supplier or missing state, returns a closed/zeroed view."""
    safe = {"open": False, "open_until": None, "consec_failures": 0}
    try:
        supplier = (supplier or "").strip()
        if not supplier:
            return safe
        con = connect()
        try:
            r = con.execute(
                "SELECT consec_failures, breaker_open_until FROM supplier_rate_state "
                "WHERE supplier=?", (supplier,)).fetchone()
        finally:
            con.close()
        if r is None:
            return safe
        open_until = r["breaker_open_until"]
        is_open = bool(open_until and open_until > _now())
        return {"open": is_open, "open_until": open_until,
                "consec_failures": _as_int(r["consec_failures"], 0)}
    except Exception as e:
        log.warning("breaker_state(%s) failed: %s", supplier, e)
        return safe

def reclaim_orphans(con=None):
    """Eagerly reclaim jobs orphaned by a crashed worker: reset 'processing' rows
    whose lease has already expired back to 'queued' so they run on the NEXT drain
    instead of waiting up to LEASE_SECONDS for the in-_claim reclaim to notice them.

    Mirrors the stale-lease reclaim in _claim (status='processing' AND
    lease_until<=now), but runs once eagerly on worker start. Idempotent: a fresh,
    non-expired lease (lease_until>now) is left untouched, so it's safe to call on
    every start. Returns the number of rows reclaimed."""
    own = con is None
    if own:
        con = connect()
    try:
        now = _now()
        cur = con.execute("""UPDATE intake_jobs SET status='queued', lease_until=NULL
                             WHERE status='processing'
                               AND lease_until IS NOT NULL AND lease_until<=?""", (now,))
        con.commit()
        n = cur.rowcount or 0
    finally:
        if own:
            con.close()
    if n:
        log.info("startup orphan-sweep reclaimed %s stale 'processing' job(s)", n)
    return n


def _strip_draft(draft):
    """Drop binary/internal keys so the draft is JSON-storable. The source PDF
    bytes are re-derived from the kept inbox file when the draft is reviewed."""
    return {k: v for k, v in draft.items() if not k.startswith("_")}

def _import_log(row, channel, status, records=0, message=""):
    """Record an import event from a queue job (best-effort)."""
    try:
        import import_log
        import_log.log(channel, row["filename"], status,
                       actor=row["uploaded_by"] or "system",
                       supplier=row["backend"], period=row["period"],
                       sha256=row["sha256"], records=records, bytes=row["size"],
                       message=message)
    except Exception as e:
        try:
            job_id = row["id"]
        except Exception:
            job_id = "?"
        log.warning("_import_log: failed to record import event for job %s: %s", job_id, e)

def _do_register(con, row):
    """Engine-side handler for a REGISTRATION job (decoupling D4): write the
    validated statement into suppliers.db OFF the web request. Returns the outcome
    string. Idempotent — register_statement upserts on its UNIQUE keys, so the
    queue's at-least-once retry can re-run this safely.

    The confirming user is propagated as the AUDIT ACTOR: audit.set_actor binds the
    thread-local actor that supplier_master.connect()'s ffs_actor() resolves, so the
    suppliers.db audit triggers record changed_by=<user>, not 'system'. This mirrors
    app.py's before/after-request actor hooks for the engine path. Note the engine
    owns suppliers.db migrations (register_statement runs db_migrate.apply); the
    read-only web app must never migrate suppliers.db."""
    import invoice_control as IC, audit
    jid = row["id"]
    p = json.loads(row["payload"])
    user = row["uploaded_by"] or "system"
    audit.set_actor(None, user)
    try:
        synced = IC.register_statement(
            p["supplier"], p["statement_ref"], p.get("period"),
            p.get("statement_date"), p.get("lines") or [],
            notes=p.get("notes"), customer=p.get("customer"))
    finally:
        audit.reset_actor()
    con.execute("""UPDATE intake_jobs SET status='done', finished_at=?,
                   error=NULL, lease_until=NULL WHERE id=?""", (_now(), jid))
    con.commit()
    _import_log(row, "register", "success", records=len(p.get("lines") or []),
                message=f"statement {p['statement_ref']} registered "
                        f"({synced} VAT-bearing synced)")
    return "done"


def _do_close(con, row):
    """Engine-side handler for a MONTHLY CLOSE job (decoupling D5's web surface): run
    engine_close.close(period) OFF the web request. Returns the outcome string.

    engine_close pulls the heavy stage modules (consolidate/build_master/history/...),
    so it is imported LAZILY here — never at module top — to keep the queue light for
    every other job kind. The requesting user is propagated as the AUDIT ACTOR (as in
    _do_register) so the close's audit trail is attributed to who pressed the button.

    The close is itself guarded by engine_close's 'close-run' process_lock: if another
    close is already running, close() raises a RuntimeError. We let that propagate to
    the dispatch except -> _fail_or_retry (channel='close'), which RE-QUEUES it with
    backoff (a generic exception is a retry, not an immediate DLQ) so this job simply
    runs once the other close finishes. Idempotent: every stage is safe to re-run."""
    import engine_close, audit
    jid = row["id"]
    p = json.loads(row["payload"])
    period = p.get("period")
    user = row["uploaded_by"] or "system"
    audit.set_actor(None, user)
    try:
        engine_close.close(period, actor=user)
    finally:
        audit.reset_actor()
    con.execute("""UPDATE intake_jobs SET status='done', finished_at=?,
                   error=NULL, lease_until=NULL WHERE id=?""", (_now(), jid))
    con.commit()
    _import_log(row, "close", "success",
                message=f"monthly close {period} complete")
    return "done"


def _do_fetch(con, row):
    """Engine-side handler for a supplier-portal FETCH job (automated-capture flagship):
    run portal_scraper.scrape OFF the web request. Returns the outcome string.

    portal_scraper pulls the adapter registry / pricing_intelligence load path, so it is
    imported LAZILY here — never at module top — to keep the queue light for every other
    job kind. The requesting user is propagated as the AUDIT ACTOR (as in _do_register/
    _do_close) so the run is attributed to who pressed the button.

    The supplier is the job's `backend`, so the per-supplier rate-limiter already governed
    CLAIM eligibility; here we drive the BREAKER with record_outcome(ok). On SUCCESS we
    reset the failure streak and mark the row done. On EXCEPTION we record the failure (so
    the breaker can trip) and RE-RAISE — record_outcome must not mask the original error —
    so the dispatch routes it to _fail_or_retry (channel='fetch') for retry/backoff."""
    import portal_scraper, audit
    jid = row["id"]
    p = json.loads(row["payload"])
    supplier = p["supplier"]
    entity = p.get("entity")
    # the breaker/limiter is keyed on the job's `backend` (= the UPPERCASED supplier set
    # by enqueue_fetch), so record outcomes against THAT key, not the raw payload value.
    governed = row["backend"]
    user = row["uploaded_by"] or "system"
    audit.set_actor(None, user)
    try:
        res = portal_scraper.scrape(supplier, entity,
                                    p.get("date_from"), p.get("date_to"))
    except Exception:
        record_outcome(governed, ok=False)      # drive the breaker, then re-raise
        raise
    finally:
        audit.reset_actor()
    record_outcome(governed, ok=True)
    con.execute("""UPDATE intake_jobs SET status='done', finished_at=?,
                   error=NULL, lease_until=NULL WHERE id=?""", (_now(), jid))
    con.commit()
    _import_log(row, "fetch", "success", records=res.get("loaded", 0),
                message=f"{res.get('loaded', 0)} loaded / {res.get('fetched', 0)} fetched")
    return "done"


def process_one():
    """Claim and process one job. Returns (job_id, outcome) or None if the queue
    is idle. Never raises — failures are recorded on the row."""
    import extract as EX
    con = connect()
    try:
        row = _claim(con)
        if not row:
            return None
        jid = row["id"]
        attempts = row["attempts"] + 1      # _claim incremented it
        kind = (row["kind"] if "kind" in row.keys() else None) or KIND_EXTRACT
        if kind == KIND_REGISTER:
            try:
                return (jid, _do_register(con, row))
            except Exception as e:
                return (jid, _fail_or_retry(con, row, jid, attempts, e, channel="register"))
        elif kind == KIND_CLOSE:
            try:
                return (jid, _do_close(con, row))
            except Exception as e:
                # A 'close-run' lock-contention RuntimeError lands here too: _fail_or_retry
                # RE-QUEUES with backoff (a generic exception is a retry, not an immediate
                # DLQ), so the close simply runs once the other close finishes.
                return (jid, _fail_or_retry(con, row, jid, attempts, e, channel="close"))
        elif kind == KIND_FETCH:
            try:
                return (jid, _do_fetch(con, row))
            except Exception as e:
                # _do_fetch already recorded the failure outcome (breaker); _fail_or_retry
                # RE-QUEUES with backoff (a generic exception is a retry, not an immediate
                # DLQ) so the fetch runs again once the supplier is eligible.
                return (jid, _fail_or_retry(con, row, jid, attempts, e, channel="fetch"))
        try:
            data = read_bytes(row["stored_path"])
            draft = EX.extract(data, row["filename"], backend=row["backend"] or None,
                               strict=True)
            if draft.get("error"):
                raise RuntimeError(draft["error"])
            con.execute("""UPDATE intake_jobs SET status='ready', finished_at=?,
                           draft=?, error=NULL, lease_until=NULL WHERE id=?""",
                        (_now(), json.dumps(_strip_draft(draft)), jid))
            con.commit()
            _import_log(row, "extract", "success", records=len(draft.get("lines", [])),
                        message=f"extracted via {draft.get('backend','')}")
            return (jid, "ready")
        except EX.TransientExtractionError as e:
            # upstream out of tokens / rate-limited. This is not the document's
            # fault, so roll the hard-attempt counter back to its pre-claim value
            # and instead track consecutive token deferrals separately.
            deferred = row["defer_count"] + 1
            if deferred >= MAX_TOKEN_RETRIES:
                # the quota isn't coming back on its own — stop auto-processing and
                # hold the job in the waiting room for a manual "Send now".
                msg = (f"held after {deferred} token-quota retries — press Send to "
                       f"retry manually ({e})")[:500]
                con.execute("""UPDATE intake_jobs SET status='held', attempts=?,
                               defer_count=?, error=?, lease_until=NULL,
                               next_attempt_at=NULL WHERE id=?""",
                            (row["attempts"], deferred, msg, jid))
                con.commit()
                return (jid, "held")
            msg = f"waiting for AI tokens (retry {deferred}/{MAX_TOKEN_RETRIES}) — {e}"[:500]
            con.execute("""UPDATE intake_jobs SET status='waiting', attempts=?,
                           defer_count=?, error=?, lease_until=NULL,
                           next_attempt_at=? WHERE id=?""",
                        (row["attempts"], deferred, msg,
                         _at(time.time() + RETRY_AFTER_TOKENS), jid))
            con.commit()
            return (jid, "waiting")
        except Exception as e:
            return (jid, _fail_or_retry(con, row, jid, attempts, e, channel="extract"))
    finally:
        con.close()


def _fail_or_retry(con, row, jid, attempts, e, channel="extract"):
    """Hard-failure handler shared by every job kind: dead-letter to 'failed' once
    MAX_ATTEMPTS is reached (logging the failure to import_log so it surfaces in the
    monitoring panel), else re-queue with capped exponential backoff. Never swallows
    silently — the error is recorded on the row and logged."""
    msg = f"{type(e).__name__}: {e}"[:500]
    log.warning("job %s (%s) failed attempt %s: %s", jid, channel, attempts, msg)
    if attempts >= MAX_ATTEMPTS:
        con.execute("""UPDATE intake_jobs SET status='failed', error=?,
                       finished_at=?, lease_until=NULL WHERE id=?""",
                    (msg, _now(), jid))
        outcome = "failed"
        _import_log(row, channel, "failed", message=msg)
    else:
        backoff = min(BACKOFF_BASE * (2 ** (attempts - 1)), BACKOFF_MAX)
        con.execute("""UPDATE intake_jobs SET status='queued', error=?,
                       lease_until=NULL, next_attempt_at=? WHERE id=?""",
                    (msg, _at(time.time() + backoff), jid))
        outcome = "retry"
    con.commit()
    return outcome

def drain(limit=50):
    """Process up to `limit` jobs, stopping when the queue is idle. Returns the
    number processed."""
    n = 0
    for _ in range(limit):
        if process_one() is None:
            break
        n += 1
    return n

def run_worker(poll_seconds=POLL_SECONDS, stop=None):
    """Drain forever, sleeping when idle. `stop` is an optional callable returning
    True to exit (used by the in-app daemon thread for clean shutdown/tests)."""
    reclaim_orphans()        # eagerly reclaim jobs left 'processing' by a crash
    while not (stop and stop()):
        if drain() == 0:
            time.sleep(poll_seconds)


# states of a job that has NOT been successfully extracted yet — these form the
# "backlog" shown in the UI ("not done" work that still needs attention).
PENDING_STATES = ("queued", "waiting", "held", "processing", "failed")

# states that represent GENUINELY in-flight work and thus gate new uploads (so a
# stuck backlog gets cleared before more piles on). 'failed'/'held' are TERMINAL
# — they need a human, not the worker — so they must NOT freeze fleet-wide intake;
# they're in PENDING_STATES (still "not done") but excluded from the upload gate.
BLOCKING_STATES = ("queued", "waiting", "processing")

# ---- reliability telemetry: DLQ size + oldest-pending-job age SLO ----------
# DLQ ("dead-letter") = jobs in the TERMINAL failed/held states: the worker is done
# with them and a human must redrive (the "Send / restart all" button -> requeue_all).
# A DLQ at/above this is worth an alert.
DLQ_ALERT_MIN = 1
# Oldest still-auto-flowing job (a BLOCKING_STATES job) older than this is a
# stalled-/starved-worker alarm: healthy jobs drain in seconds, so a multi-hour-old
# queued/waiting/processing row means nothing is making progress.
OLDEST_PENDING_SLO_HOURS = 6


def _parse_uploaded_at(s):
    """Parse an `uploaded_at` value (stored UTC '%Y-%m-%d %H:%M:%S', same format as
    _now()) into a naive UTC datetime, tolerating a trailing fractional second or 'Z'
    defensively. Returns None if it can't be parsed."""
    if not s:
        return None
    s = str(s).strip().rstrip("Z").strip()
    if "." in s:                              # drop a fractional-second tail if present
        s = s.split(".", 1)[0]
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def queue_health(con=None):
    """Operational telemetry for the intake queue: DLQ size (terminal failed/held jobs
    needing a human redrive) and the age of the oldest still-auto-flowing job (an SLO
    on worker progress — a high value means the worker is stalled or starved).

    Never raises: on any internal error it logs and returns a safe zeroed dict, so a
    monitoring surface can render it without a try/except of its own (callers still
    guard the render to be safe)."""
    safe = {"failed": 0, "held": 0, "dlq": 0,
            "oldest_pending_age_s": None, "oldest_pending_id": None,
            "oldest_pending_uploaded_at": None, "dlq_growth_24h": 0,
            "dlq_breach": False, "age_breach": False}
    own = con is None
    try:
        if own:
            con = connect()
        c = counts()
        failed = c.get("failed", 0)
        held = c.get("held", 0)
        dlq = failed + held
        ph = ",".join("?" * len(BLOCKING_STATES))
        row = con.execute(
            f"""SELECT id, uploaded_at FROM intake_jobs WHERE status IN ({ph})
                AND uploaded_at IS NOT NULL ORDER BY uploaded_at ASC LIMIT 1""",
            tuple(BLOCKING_STATES)).fetchone()
        age_s = oldest_id = oldest_at = None
        if row is not None:
            dt = _parse_uploaded_at(row["uploaded_at"])
            if dt is not None:
                age_s = max(0, int((datetime.datetime.utcnow() - dt).total_seconds()))
                oldest_id = row["id"]
                oldest_at = row["uploaded_at"]
        growth = dlq_growth(24, con=con, current_dlq=dlq)
        return {
            "failed": failed, "held": held, "dlq": dlq,
            "oldest_pending_age_s": age_s, "oldest_pending_id": oldest_id,
            "oldest_pending_uploaded_at": oldest_at,
            "dlq_growth_24h": growth,
            "dlq_breach": dlq >= DLQ_ALERT_MIN,
            "age_breach": age_s is not None and age_s >= OLDEST_PENDING_SLO_HOURS * 3600,
        }
    except Exception as e:
        log.warning("queue_health failed: %s", e)
        return safe
    finally:
        if own and con is not None:
            con.close()


def record_health_sample(con=None):
    """Append one DLQ-size sample (ts, dlq=failed+held) to intake_health_samples and
    prune rows older than ~7 days. Cheap/idempotent and NEVER raises — it's called on
    every scheduler tick to accumulate the growth-rate history. Returns the sampled
    DLQ size (or None on error)."""
    own = con is None
    try:
        if own:
            con = connect()
        c = counts()
        dlq = c.get("failed", 0) + c.get("held", 0)
        cutoff = _at(time.time() - 7 * 86400)
        con.execute("INSERT INTO intake_health_samples (ts, dlq) VALUES (?, ?)",
                    (_now(), dlq))
        con.execute("DELETE FROM intake_health_samples WHERE ts < ?", (cutoff,))
        con.commit()
        return dlq
    except Exception as e:
        log.warning("record_health_sample failed: %s", e)
        return None
    finally:
        if own and con is not None:
            con.close()


def dlq_growth(window_hours=24, con=None, current_dlq=None):
    """Net DLQ growth over the trailing `window_hours`: current DLQ minus the SMALLEST
    sampled DLQ within the window (so it reports how much the pile has grown since its
    recent low). Returns 0 when there is no sample in the window (or on error). Never
    raises."""
    own = con is None
    try:
        if own:
            con = connect()
        if current_dlq is None:
            c = counts()
            current_dlq = c.get("failed", 0) + c.get("held", 0)
        since = _at(time.time() - window_hours * 3600)
        row = con.execute(
            "SELECT MIN(dlq) m FROM intake_health_samples WHERE ts >= ?",
            (since,)).fetchone()
        if row is None or row["m"] is None:
            return 0
        return max(0, current_dlq - int(row["m"]))
    except Exception as e:
        log.warning("dlq_growth failed: %s", e)
        return 0
    finally:
        if own and con is not None:
            con.close()

def _parse_ts(s):
    """Parse a stored UTC timestamp ('%Y-%m-%d %H:%M:%S') into a naive UTC datetime.
    Alias of _parse_uploaded_at — every TEXT time column on intake_jobs (uploaded_at,
    started_at, finished_at) is written in the same format by _now()/_at()."""
    return _parse_uploaded_at(s)


def _error_key(err):
    """Normalise a raw `error` message into a short, groupable failure-reason key.
    Prefer a leading 'ExceptionType:' token (that's how _fail_or_retry formats
    failures); else fall back to the first line, trimmed to ~60 chars. Returns ''
    for an empty error so non-failures don't pollute the histogram."""
    if not err:
        return ""
    first = str(err).strip().splitlines()[0].strip() if str(err).strip() else ""
    if not first:
        return ""
    # "ExceptionType: detail" -> keep through the type token + a little context
    if ":" in first:
        head = first.split(":", 1)[0].strip()
        # a bare TypeName (no spaces) is the canonical _fail_or_retry shape -> use it
        if head and " " not in head:
            return first[:60].strip()
    return first[:60].strip()


# terminal status buckets the scorecard scores on (see reliability_scorecard).
_TERMINAL_OK = ("done",)               # successfully completed work
_TERMINAL_BAD = ("failed", "held")     # needs a human — counts against success
_PENDING_STATES_RC = ("queued", "waiting", "processing")  # still in flight


def reliability_scorecard(con=None):
    """Read-only supplier/channel PROCESSING-RELIABILITY scorecard over intake_jobs.
    Pure analytics: it NEVER mutates the queue and NEVER raises — on any internal
    error it logs a warning and returns the safe empty structure below (matching
    queue_health's contract, so a render surface needs no try/except of its own).

    Return shape (a dict)::

        {
          "channels": [          # one row per `backend` channel, busiest first
            {
              "channel": str,            # backend; 'auto' when the column is NULL
              "total": int,              # all jobs on this channel
              "done": int, "failed": int, "held": int, "ready": int,
              "pending": int,            # queued + waiting + processing (in flight)
              "success_rate": float|None,# done / (done+failed+held); None if no
                                         #   terminal (done/failed/held) jobs yet
              "retry_rate": float|None,  # fraction of jobs with attempts > 1;
                                         #   None when total == 0
              "median_duration_s": float|None,  # over jobs with BOTH started_at
              "avg_duration_s": float|None,      #   and finished_at (else excluded)
              "duration_n": int,         # how many jobs the durations are over
              "top_error": str,          # most common normalised failure reason
                                         #   on this channel ('' if no failures)
            }, ...
          ],
          "failure_reasons": [  # histogram across ALL failed+held jobs, desc by count
              (reason_key:str, count:int), ...
          ],
          "suppliers": [        # BEST-EFFORT, secondary: resolvable only for
                                #   ready/done jobs (the draft carries the supplier).
                                #   Failures have no supplier, so this is success-only.
              {"supplier": str, "ready": int, "done": int, "total_resolved": int},
              ...
          ],
        }

    success_rate is deliberately over TERMINAL outcomes only (done vs failed/held) so
    a big in-flight backlog doesn't depress a channel's score; pending jobs are
    reported separately. Durations are end-to-end processing time
    (finished_at - started_at) and skip any row missing either timestamp."""
    safe = {"channels": [], "failure_reasons": [], "suppliers": []}
    own = con is None
    try:
        if own:
            con = connect()
        rows = con.execute(
            "SELECT backend, status, attempts, started_at, finished_at, error, draft "
            "FROM intake_jobs").fetchall()

        chans = {}              # channel -> accumulator dict
        reasons = {}            # normalised failure reason -> count
        suppliers = {}          # supplier -> {ready, done}

        def _chan(name):
            return chans.setdefault(name, {
                "channel": name, "total": 0, "done": 0, "failed": 0, "held": 0,
                "ready": 0, "pending": 0, "retried": 0, "durations": [],
                "errors": {}})

        for r in rows:
            ch = (r["backend"] or "auto")
            st = r["status"]
            acc = _chan(ch)
            acc["total"] += 1
            if st in ("done", "failed", "held", "ready"):
                acc[st] += 1
            elif st in _PENDING_STATES_RC:
                acc["pending"] += 1
            # retry rate: a job that took more than one claim/attempt
            try:
                if r["attempts"] is not None and int(r["attempts"]) > 1:
                    acc["retried"] += 1
            except (TypeError, ValueError) as e:
                log.debug("retry-rate stat: unparseable attempts value: %s", e)
            # end-to-end duration only when BOTH timestamps parse
            t0 = _parse_ts(r["started_at"])
            t1 = _parse_ts(r["finished_at"])
            if t0 is not None and t1 is not None:
                d = (t1 - t0).total_seconds()
                if d >= 0:
                    acc["durations"].append(d)
            # failure-reason histogram (per-channel top + overall), only for the
            # terminal-bad states that actually represent a failure.
            if st in _TERMINAL_BAD:
                key = _error_key(r["error"])
                if key:
                    acc["errors"][key] = acc["errors"].get(key, 0) + 1
                    reasons[key] = reasons.get(key, 0) + 1
            # best-effort supplier (only resolvable from a ready/done draft)
            if st in ("ready", "done") and r["draft"]:
                try:
                    sup = json.loads(r["draft"]).get("supplier")
                except (ValueError, TypeError):
                    sup = None
                if sup:
                    s = suppliers.setdefault(sup, {"ready": 0, "done": 0})
                    if st in ("ready", "done"):
                        s[st] += 1

        channels = []
        for acc in chans.values():
            terminal = acc["done"] + acc["failed"] + acc["held"]
            success_rate = (acc["done"] / terminal) if terminal else None
            retry_rate = (acc["retried"] / acc["total"]) if acc["total"] else None
            durs = sorted(acc["durations"])
            n = len(durs)
            if n:
                avg = sum(durs) / n
                mid = n // 2
                median = durs[mid] if n % 2 else (durs[mid - 1] + durs[mid]) / 2
            else:
                avg = median = None
            top_error = ""
            if acc["errors"]:
                top_error = max(acc["errors"].items(), key=lambda kv: (kv[1], kv[0]))[0]
            channels.append({
                "channel": acc["channel"], "total": acc["total"],
                "done": acc["done"], "failed": acc["failed"], "held": acc["held"],
                "ready": acc["ready"], "pending": acc["pending"],
                "success_rate": success_rate, "retry_rate": retry_rate,
                "median_duration_s": median, "avg_duration_s": avg,
                "duration_n": n, "top_error": top_error,
            })
        # busiest channel first; ties broken by name for a stable order
        channels.sort(key=lambda c: (-c["total"], c["channel"]))

        failure_reasons = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))

        sup_rows = [{"supplier": k, "ready": v["ready"], "done": v["done"],
                     "total_resolved": v["ready"] + v["done"]}
                    for k, v in suppliers.items()]
        sup_rows.sort(key=lambda s: (-s["total_resolved"], s["supplier"]))

        return {"channels": channels, "failure_reasons": failure_reasons,
                "suppliers": sup_rows}
    except Exception as e:
        log.warning("reliability_scorecard failed: %s", e)
        return safe
    finally:
        if own and con is not None:
            con.close()


# ---------------------------------------------------------------- queries / UI
def counts():
    con = connect()
    rows = con.execute("SELECT status, COUNT(*) n FROM intake_jobs GROUP BY status").fetchall()
    con.close()
    out = {s: 0 for s in ("queued", "waiting", "held", "processing", "ready", "failed", "done")}
    for r in rows:
        out[r["status"]] = r["n"]
    return out

def pending_count(states=PENDING_STATES):
    """How many documents are still awaiting successful extraction (queued,
    waiting, held, processing, or failed). 'ready' and 'done' don't count.
    Pass `states=BLOCKING_STATES` for the upload gate, which must ignore the
    TERMINAL failed/held states so one un-actioned doc can't freeze intake."""
    con = connect()
    ph = ",".join("?" * len(states))
    n = con.execute(f"SELECT COUNT(*) FROM intake_jobs WHERE status IN ({ph})",
                    tuple(states)).fetchone()[0]
    con.close()
    return n

def requeue_all(states=("waiting", "held", "failed")):
    """Bulk 'send / restart workflow': reset every job in `states` back to 'queued'
    for immediate reprocessing (clearing backoff/retry gates and counters). Returns
    the number of jobs reset. The background worker (or a drain()) then processes
    them; nothing is lost — the source bytes are untouched."""
    con = connect()
    ph = ",".join("?" * len(states))
    ids = [r[0] for r in con.execute(
        f"SELECT id FROM intake_jobs WHERE status IN ({ph})", tuple(states)).fetchall()]
    if ids:
        con.execute(f"""UPDATE intake_jobs SET status='queued', attempts=0, defer_count=0,
                        lease_until=NULL, next_attempt_at=NULL, error=NULL,
                        started_at=NULL, finished_at=NULL, draft=NULL
                        WHERE status IN ({ph})""", tuple(states))
        con.commit()
    con.close()
    return len(ids)

def jobs(status=None, limit=100):
    con = connect()
    if status:
        rows = con.execute("""SELECT * FROM intake_jobs WHERE status=?
                              ORDER BY id DESC LIMIT ?""", (status, limit)).fetchall()
    else:
        rows = con.execute("SELECT * FROM intake_jobs ORDER BY id DESC LIMIT ?",
                           (limit,)).fetchall()
    con.close()
    # Exclude the P1 tenant_id plumbing from the surfaced job-row contract (the
    # monitoring panel / monitor_rows consume these dicts by key) — adding a
    # multi-tenancy column must not change the exposed shape.
    return [_drop_tenant(dict(r)) for r in rows]

# ordering for the monitoring panel: the STUCK states (failed/held need a human,
# waiting is auto-retrying) float to the top, then in-flight, then the rest — newest
# first within each band.
_MONITOR_RANK = {"failed": 0, "held": 1, "waiting": 2, "processing": 3,
                 "queued": 4, "ready": 5, "done": 6}

def monitor_rows(limit=100):
    """Active/recent jobs for the upload-monitoring panel: stuck jobs (failed/held/
    waiting) first, then newest. Each row carries the queue fields PLUS the
    supplier/confidence the extractor resolved into the draft (when a job is ready),
    so the panel can show the extraction outcome without re-running anything."""
    out = []
    for j in jobs(limit=limit):
        supplier = confidence = None
        if j.get("draft"):
            try:
                d = json.loads(j["draft"])
                supplier = d.get("supplier")
                confidence = d.get("confidence")
            except (ValueError, TypeError) as e:
                # a malformed draft must not break the monitor view — skip the
                # supplier/confidence enrichment for this row, but log the swallow.
                log.warning("monitor_rows: skipping malformed draft for job %s (%s)",
                            j.get("id"), e)
        j["draft_supplier"] = supplier
        j["draft_confidence"] = confidence
        out.append(j)
    out.sort(key=lambda r: (_MONITOR_RANK.get(r["status"], 9), -r["id"]))
    return out

def get_job(job_id):
    con = connect()
    r = con.execute("SELECT * FROM intake_jobs WHERE id=?", (job_id,)).fetchone()
    con.close()
    # Exclude the P1 tenant_id plumbing from the surfaced job-row contract (callers
    # consume this dict by key) — adding a multi-tenancy column must not change the
    # exposed shape.
    return _drop_tenant(dict(r)) if r else None

def get_draft(job_id):
    """Return (draft_dict, pdf_bytes_list) for a `ready` job, re-deriving the
    source PDF bytes from the kept inbox file so the existing review/confirm flow
    works unchanged. Returns (None, None) if not ready."""
    import extract as EX
    r = get_job(job_id)
    if not r or r["status"] != "ready" or not r["draft"]:
        return None, None
    draft = json.loads(r["draft"])
    pdfs = EX.unpack(read_bytes(r["stored_path"]), r["filename"])
    return draft, [b for _name, b in pdfs]

def complete(job_id):
    """Mark a job done after a human has reviewed+committed its draft, and remove
    the now-redundant inbox bytes (they have been attached to the document vault
    by the confirm step). Idempotent."""
    r = get_job(job_id)
    if not r:
        return False
    con = connect()
    con.execute("UPDATE intake_jobs SET status='done', finished_at=? WHERE id=?",
                (_now(), job_id))
    con.commit(); con.close()
    try:
        if r["stored_path"]:
            os.remove(_safe_inbox(r["stored_path"]))
    except OSError as e:
        log.debug("mark-done: could not remove inbox bytes for job %s: %s", job_id, e)
    return True

def requeue(job_id):
    """Force a job back to 'queued' for immediate reprocessing, clearing any
    backoff/retry gate and the attempt counter. Works from any state (failed,
    waiting, ready) — e.g. an admin clicking 'Retry now' on a job that is waiting
    for AI tokens, or retrying a failure. Returns False if the job/file is gone."""
    r = get_job(job_id)
    if not r:
        return False
    # a fileless job (registration / monthly close) carries its payload, not inbox bytes
    # — it is retryable as long as the payload survives; an extraction job needs its
    # source file present.
    is_fileless = (r["kind"] if "kind" in r.keys() else None) in (KIND_REGISTER, KIND_CLOSE, KIND_FETCH)
    if not is_fileless and (not r["stored_path"] or not os.path.exists(_safe_inbox(r["stored_path"]))):
        return False
    con = connect()
    con.execute("""UPDATE intake_jobs SET status='queued', attempts=0, defer_count=0,
                   lease_until=NULL, next_attempt_at=NULL, error=NULL, started_at=NULL,
                   finished_at=NULL, draft=NULL WHERE id=?""", (job_id,))
    con.commit(); con.close()
    return True

def discard(job_id):
    """Drop a job without processing (e.g. an erroneous upload). Removes the inbox
    bytes and the row."""
    r = get_job(job_id)
    if not r:
        return False
    con = connect()
    con.execute("DELETE FROM intake_jobs WHERE id=?", (job_id,))
    con.commit(); con.close()
    try:
        if r["stored_path"]:
            os.remove(_safe_inbox(r["stored_path"]))
    except OSError as e:
        log.debug("discard: could not remove inbox bytes for job %s: %s", job_id, e)
    return True


# ---------------------------------------------------------------- CLI / self-test
if __name__ == "__main__":
    import sys
    if "--work" in sys.argv:
        print(f"intake worker draining {DB} (Ctrl-C to stop)…")
        try:
            run_worker()
        except KeyboardInterrupt:
            print("\nstopped.")
    elif "--once" in sys.argv:
        print(f"processed {drain()} job(s)")
    elif "--status" in sys.argv:
        for s, n in counts().items():
            print(f"  {s:11} {n}")
    else:
        # offline smoke test: stub the extractor so the queue mechanics (durability,
        # dedupe, claim, ready) are exercised without needing a real PDF/AI call.
        import tempfile, shutil, extract as EX
        EX.extract = lambda data, name, backend=None, strict=False: {
            "supplier": "DEMO", "lines": [{"invoice_no": "X1", "net": 100, "vat": 21}],
            "backend": backend or "stub", "confidence": "low", "_pdf_bytes": []}
        d = tempfile.mkdtemp()
        DB = os.path.join(d, "intake.db"); INBOX = os.path.join(d, "inbox")
        jid, st = enqueue(b"%PDF-1.4 demo", "demo.pdf", backend="none",
                          period="2026-05", user="tester")
        print("enqueued", jid, st)
        jid2, _ = enqueue(b"%PDF-1.4 demo", "demo.pdf", backend="none")   # idempotent
        assert jid2 == jid, "dedupe failed"
        print("processed ->", process_one())
        print("counts:", counts())
        assert counts()["ready"] == 1, "expected one ready draft"
        shutil.rmtree(d, ignore_errors=True)
        print("self-test OK")
