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


# ---------------------------------------------------------------- claim + process
def _claim(con):
    """Atomically take the next eligible job (oldest ready-to-run, or a stale
    lease to reclaim). BEGIN IMMEDIATE serialises workers so a job is claimed
    once. Returns the pre-update row or None."""
    now = _now()
    con.execute("BEGIN IMMEDIATE")
    row = con.execute("""SELECT * FROM intake_jobs WHERE
          (status IN ('queued','waiting') AND (next_attempt_at IS NULL OR next_attempt_at<=?))
       OR (status='processing' AND lease_until IS NOT NULL AND lease_until<=?)
        ORDER BY id LIMIT 1""", (now, now)).fetchone()
    if not row:
        con.execute("COMMIT")
        return None
    con.execute("""UPDATE intake_jobs SET status='processing', attempts=attempts+1,
                   lease_until=?, started_at=COALESCE(started_at,?), error=NULL
                   WHERE id=?""",
                (_at(time.time() + LEASE_SECONDS), now, row["id"]))
    con.execute("COMMIT")
    return row

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
    return [dict(r) for r in rows]

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
    return dict(r) if r else None

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
    # a registration job carries its payload (no inbox bytes) — it is retryable as
    # long as the payload survives; an extraction job needs its source file present.
    is_register = (r["kind"] if "kind" in r.keys() else None) == KIND_REGISTER
    if not is_register and (not r["stored_path"] or not os.path.exists(_safe_inbox(r["stored_path"]))):
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
