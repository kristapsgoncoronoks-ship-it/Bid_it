import sqlite3
import db
import db_migrate
"""
AUDIT LAYER - automatic change history for ALL databases.

Every INSERT / UPDATE / DELETE on registered tables is captured by SQLite
triggers into an audit_log table INSIDE the same database file (history travels
with the data). Captured per change: timestamp (UTC), table, row key, action,
full OLD row and NEW row snapshots as JSON.

Because logging is done by triggers, it catches writes from the web UI, the
CLI tools, the monthly pipeline, and even manual sqlite3 edits - nothing can
change data unlogged.

API:
    install_audit(con, tables)            create log + triggers + one-time BASELINE snapshots
    history(con, table=None, key_like=None, dt_from=None, dt_till=None, action=None)
                                          -> change log rows, date-range filterable
    as_of(con, table, ts)                 -> reconstructed table content as of a timestamp
    record_history(con, table, key)       -> full life story of one record
"""
import json, threading

LOG_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    tbl TEXT, rowkey TEXT, action TEXT,
    old_data TEXT, new_data TEXT, changed_by TEXT);
CREATE INDEX IF NOT EXISTS ix_audit_tk ON audit_log(tbl, rowkey, ts);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(ts);
CREATE TABLE IF NOT EXISTS audit_context (actor TEXT);
"""

# The actor that owns the CURRENT unit of work is held in thread-local storage,
# NOT in a shared DB row. Under a threaded WSGI server (waitress) each request is
# served on its own worker thread, so each thread sees its own actor with zero
# cross-request interleave. Audit triggers read it through the per-connection
# application-defined function ffs_actor() registered by bind() — the value is
# resolved fresh for every row the trigger touches, on the very connection doing
# the write. (The previous design stored the actor in a single committed
# audit_context row shared across all connections, which raced under concurrency
# and could attribute one user's change to another.)
ACTOR_SQL = "ffs_actor()"

_local = threading.local()

def _current_actor():
    return getattr(_local, "actor", "system")

def bind(con):
    """Register ffs_actor() on this connection so its audit triggers can read the
    current thread's actor. MUST be called on every connection that may write to
    an audited table (install_audit() does this for callers that go through it)."""
    con.create_function("ffs_actor", 0, _current_actor)

def set_actor(con, user):
    """Attribute subsequent changes by the CURRENT thread to `user`. Call
    reset_actor() when the unit of work (e.g. a web request) finishes. `con` is
    bound so direct callers' connections can resolve ffs_actor()."""
    _local.actor = user
    if con is not None:
        bind(con)

def reset_actor(con=None):
    _local.actor = "system"

def _cols_pk(con, table):
    info = con.execute(f"PRAGMA table_info({table})").fetchall()
    # BLOB columns (e.g. password salt/hash) are excluded from snapshots:
    # binary data breaks json_object and secrets must never enter the audit log
    cols = [r[1] for r in info if (r[2] or "").upper() != "BLOB"]
    pks = [r[1] for r in info if r[5]]
    return cols, (pks[0] if pks else "rowid")

def _jobj(prefix, cols):
    return "json_object(" + ", ".join(f"'{c}', {prefix}.{c}" for c in cols) + ")"

_AUDIT_INSTALLED = set()   # (db_file, frozenset(tables)) already set up this process

def _db_file(con):
    try:
        for _seq, name, file in con.execute("PRAGMA database_list"):
            if name == "main":
                return file or ":memory:"
    except db.DBError:
        pass
    return ":memory:"

def install_audit(con, tables):
    # The audit_log table and the per-table triggers PERSIST in the database file,
    # so this DDL only needs to run once per DB per process. Re-running it on every
    # connect() (which the module connect() helpers do) was the dominant request
    # cost. Skip the work when we have already installed it this process (never
    # cache :memory: DBs, whose path collides across distinct connections).
    bind(con)   # always — triggers call ffs_actor(); register it per connection
    path = _db_file(con)
    key = (path, frozenset(tables))
    if path != ":memory:" and key in _AUDIT_INSTALLED:
        return
    con.executescript(LOG_DDL)
    db_migrate.apply(con, "audit", ["ALTER TABLE audit_log ADD COLUMN changed_by TEXT"])
    for t in tables:
        cols, pk = _cols_pk(con, t)
        oldj, newj = _jobj("OLD", cols), _jobj("NEW", cols)
        # rebuild triggers that predate user attribution
        old_trig = con.execute("""SELECT sql FROM sqlite_master WHERE type='trigger'
                                  AND name=?""", (f"aud_{t}_i",)).fetchone()
        if old_trig and ("changed_by" not in (old_trig[0] or "")
                         or "/*v3*/" not in (old_trig[0] or "")):
            for sfx in ("i","u","d"):
                con.execute(f"DROP TRIGGER IF EXISTS aud_{t}_{sfx}")
        con.executescript(f"""
CREATE TRIGGER IF NOT EXISTS aud_{t}_i AFTER INSERT ON {t} BEGIN /*v3*/
  INSERT INTO audit_log (tbl, rowkey, action, new_data, changed_by)
  VALUES ('{t}', NEW.{pk}, 'INSERT', {newj}, {ACTOR_SQL}); END;
CREATE TRIGGER IF NOT EXISTS aud_{t}_u AFTER UPDATE ON {t} BEGIN
  INSERT INTO audit_log (tbl, rowkey, action, old_data, new_data, changed_by)
  VALUES ('{t}', NEW.{pk}, 'UPDATE', {oldj}, {newj}, {ACTOR_SQL}); END;
CREATE TRIGGER IF NOT EXISTS aud_{t}_d AFTER DELETE ON {t} BEGIN
  INSERT INTO audit_log (tbl, rowkey, action, old_data, changed_by)
  VALUES ('{t}', OLD.{pk}, 'DELETE', {oldj}, {ACTOR_SQL}); END;""")
        # one-time BASELINE snapshot so as_of() works from installation day
        if not con.execute("SELECT 1 FROM audit_log WHERE tbl=? AND action='BASELINE' LIMIT 1",
                           (t,)).fetchone():
            for row in con.execute(f"SELECT {pk} AS k, {_jobj(t, cols)} AS j FROM {t}"):
                con.execute("""INSERT INTO audit_log (tbl, rowkey, action, new_data)
                               VALUES (?,?, 'BASELINE', ?)""", (t, row[0], row[1]))
    con.commit()
    if path != ":memory:":
        _AUDIT_INSTALLED.add(key)

def history(con, table=None, key_like=None, dt_from=None, dt_till=None, action=None, limit=500):
    w, p = ["1=1"], []
    if table:   w.append("tbl=?");            p.append(table)
    if key_like:w.append("rowkey LIKE ?");    p.append(f"%{key_like}%")
    if dt_from: w.append("ts >= ?");          p.append(dt_from)
    if dt_till: w.append("ts <= ?");          p.append(dt_till + (" 23:59:59" if len(dt_till) == 10 else ""))
    if action:  w.append("action=?");         p.append(action)
    return con.execute(f"""SELECT id, ts, tbl, rowkey, action, old_data, new_data,
                           COALESCE(changed_by,'system') AS changed_by
                           FROM audit_log WHERE {' AND '.join(w)}
                           ORDER BY ts DESC, id DESC LIMIT {int(limit)}""", p).fetchall()

def diff(old_json, new_json):
    """Human-readable field-level diff of two snapshots."""
    o = json.loads(old_json) if old_json else {}
    n = json.loads(new_json) if new_json else {}
    out = []
    for k in sorted(set(o) | set(n)):
        if o.get(k) != n.get(k):
            out.append(f"{k}: {o.get(k)!r} -> {n.get(k)!r}")
    return "; ".join(out)

def as_of(con, table, ts):
    """Reconstruct the table content as of timestamp ts (from BASELINE + changes)."""
    rows = con.execute("""SELECT rowkey, action, new_data FROM audit_log
                          WHERE tbl=? AND ts<=? ORDER BY ts, id""", (table, ts)).fetchall()
    state = {}
    for k, action, newj in rows:
        if action == "DELETE":
            state.pop(k, None)
        else:
            state[k] = json.loads(newj)
    return list(state.values())

def record_history(con, table, key):
    return history(con, table=table, key_like=key, limit=200)

def record_event(con, table, key, action, detail=None):
    """Log an EXPLICIT (non-trigger) auditable action — e.g. an evidence-pack
    export or other read/export action that has no row mutation for a trigger to
    catch. Written into the same audit_log so it shows in the admin history,
    attributed to the current thread's actor (ffs_actor()/changed_by). `detail`
    is stored as the new_data JSON snapshot. Never use for data mutations — those
    are captured automatically by the table triggers."""
    install_audit(con, [])   # ensure audit_log exists + actor is bound
    con.execute("""INSERT INTO audit_log (tbl, rowkey, action, new_data, changed_by)
                   VALUES (?,?,?,?, ffs_actor())""",
                (table, str(key), action,
                 json.dumps(detail) if detail is not None else None))
    con.commit()
