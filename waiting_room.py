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

WORKDIR = os.path.dirname(os.path.abspath(__file__))
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
    if DB != ":memory:" and DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        db_migrate.apply(con, "waiting_room",
                         ["ALTER TABLE intake_jobs ADD COLUMN defer_count INTEGER DEFAULT 0"])
        _SCHEMA_READY.add(DB)
    elif DB == ":memory:":
        con.executescript(SCHEMA)
    return con


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
        (sha256, filename, size, backend, period, uploaded_by, stored_path, status)
        VALUES (?,?,?,?,?,?,?, 'queued')""",
        (sha, filename, len(data), backend, period, user, stored))
    con.commit()                            # only now is the job visible/durable
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
            msg = f"{type(e).__name__}: {e}"[:500]
            if attempts >= MAX_ATTEMPTS:
                con.execute("""UPDATE intake_jobs SET status='failed', error=?,
                               finished_at=?, lease_until=NULL WHERE id=?""",
                            (msg, _now(), jid))
                outcome = "failed"
                _import_log(row, "extract", "failed", message=msg)
            else:
                backoff = min(BACKOFF_BASE * (2 ** (attempts - 1)), BACKOFF_MAX)
                con.execute("""UPDATE intake_jobs SET status='queued', error=?,
                               lease_until=NULL, next_attempt_at=? WHERE id=?""",
                            (msg, _at(time.time() + backoff), jid))
                outcome = "retry"
            con.commit()
            return (jid, outcome)
    finally:
        con.close()

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
# "backlog" that blocks new uploads until cleared.
PENDING_STATES = ("queued", "waiting", "held", "processing", "failed")

# ---------------------------------------------------------------- queries / UI
def counts():
    con = connect()
    rows = con.execute("SELECT status, COUNT(*) n FROM intake_jobs GROUP BY status").fetchall()
    con.close()
    out = {s: 0 for s in ("queued", "waiting", "held", "processing", "ready", "failed", "done")}
    for r in rows:
        out[r["status"]] = r["n"]
    return out

def pending_count():
    """How many documents are still awaiting successful extraction (queued,
    waiting, held, processing, or failed). 'ready' and 'done' don't count."""
    con = connect()
    ph = ",".join("?" * len(PENDING_STATES))
    n = con.execute(f"SELECT COUNT(*) FROM intake_jobs WHERE status IN ({ph})",
                    PENDING_STATES).fetchone()[0]
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
            except (ValueError, TypeError):
                pass            # a malformed draft must not break the monitor view
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
    if not r or not r["stored_path"] or not os.path.exists(_safe_inbox(r["stored_path"])):
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
