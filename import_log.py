"""
DATA-IMPORT LOG — a durable record of every data-import attempt and its outcome.

Whenever invoice data is imported — a file uploaded to the waiting room, an extraction
processed, a statement registered, a portal scraped — an entry is written here with the
outcome (success / partial / failed), the acting user, the source file (and a link to
its permanent archive in the data lake), the record count and a message. Reporting on
the /imports page filters by channel, status, client, supplier and date.

Append-only by design: this is the audit trail of what came in and whether it landed.
Stored in import_log.db (git-ignored; included in backups). Never raises — logging a
failure must not itself fail an import.
"""
import os, sqlite3

import db_tuning

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("IMPORT_LOG_DB", f"{WORKDIR}/import_log.db")
_READY = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_log (
    id INTEGER PRIMARY KEY,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    actor TEXT,
    channel TEXT,                      -- upload | extract | statement | portal | ingest
    client TEXT, supplier TEXT, period TEXT,
    source_name TEXT, sha256 TEXT, file_locator TEXT,
    status TEXT,                       -- success | partial | failed | received
    records INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0,
    message TEXT);
CREATE INDEX IF NOT EXISTS ix_implog_ts ON import_log(ts);
CREATE INDEX IF NOT EXISTS ix_implog_chan ON import_log(channel, status);
"""


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)
    if DB == ":memory:" or DB not in _READY:
        con.executescript(SCHEMA)
        if DB != ":memory:":
            _READY.add(DB)
    return con


def log(channel, source_name, status, actor="system", client=None, supplier=None,
        period=None, sha256=None, file_locator=None, records=0, bytes=0, message=""):
    """Append one import event. Returns the row id (or None on a logging error —
    logging must never break the import it is recording)."""
    try:
        con = connect()
        cur = con.execute("""INSERT INTO import_log
            (actor, channel, client, supplier, period, source_name, sha256, file_locator,
             status, records, bytes, message)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (actor, channel, client, supplier, period, source_name, sha256, file_locator,
             status, int(records or 0), int(bytes or 0), (message or "")[:1000]))
        con.commit(); rid = cur.lastrowid; con.close()
        return rid
    except Exception:
        return None


def recent(channel=None, status=None, client=None, supplier=None,
           date_from=None, date_to=None, limit=500):
    con = connect()
    w, p = ["1=1"], []
    if channel:  w.append("channel=?");  p.append(channel)
    if status:   w.append("status=?");   p.append(status)
    if client:   w.append("client=?");   p.append(client)
    if supplier: w.append("supplier=?"); p.append(supplier)
    if date_from: w.append("ts>=?");     p.append(date_from)
    if date_to:   w.append("ts<=?");     p.append(date_to + (" 23:59:59" if len(date_to) == 10 else ""))
    rows = [dict(r) for r in con.execute(
        f"SELECT * FROM import_log WHERE {' AND '.join(w)} ORDER BY id DESC LIMIT ?",
        p + [limit])]
    con.close()
    return rows


def summary(days=30):
    con = connect()
    rows = con.execute(
        "SELECT status, COUNT(*) n FROM import_log WHERE ts >= datetime('now', ?) GROUP BY status",
        (f"-{int(days)} days",)).fetchall()
    con.close()
    out = {s: 0 for s in ("received", "success", "partial", "failed")}
    for r in rows:
        out[r["status"]] = r["n"]
    return out

def filters():
    """Distinct clients/suppliers/channels seen, for the report's filter selects."""
    con = connect()
    f = {k: [r[0] for r in con.execute(
                f"SELECT DISTINCT {col} FROM import_log WHERE {col} IS NOT NULL AND {col}<>'' ORDER BY 1")]
         for k, col in (("clients", "client"), ("suppliers", "supplier"), ("channels", "channel"))}
    con.close()
    return f
