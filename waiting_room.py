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
    except (OSError, AttributeError):
        pass                                # not supported (e.g. Windows) — best effort
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
    except Exception:
        pass

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
    except OSError:
        pass
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
    except OSError:
        pass
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
