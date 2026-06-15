"""
DATABASE ABSTRACTION - single place that opens connections, so the whole system
can move from SQLite (default) to PostgreSQL by changing one environment variable,
without editing every module.

    DB_ENGINE=sqlite      (default) - file-based, zero setup, ideal single-server
    DB_ENGINE=postgres    - set DB_DSN=postgresql://user:pass@host/dbname

How modules use it: customer_master / supplier_master / vat_refund call db.connect(name).
For sqlite that returns a connection to <name>.db as today. For postgres it returns
a psycopg connection to one database with <name> as a schema, preserving separation.

Migration path (when you outgrow SQLite - many concurrent writers):
  1. pip install psycopg[binary]
  2. create the postgres DB and schemas customers/suppliers/fuel_history/security
  3. export DB_ENGINE=postgres DB_DSN=postgresql://...
  4. run db.py --migrate to copy every table from the .db files into postgres
  5. restart - the app uses postgres.

HONEST STATUS — read before flipping DB_ENGINE in production:
  The Postgres branch below opens connections, maps SQLite column types to real
  Postgres types on migration, returns dict-style rows so `row["col"]` keeps
  working, AND now wraps the connection in a paramstyle shim (_PgShim) that
  translates the modules' SQLite qmark SQL ("... VALUES (?)") to psycopg's pyformat
  ("%s") on every statement — see qmark_to_pyformat() (unit-tested). The shim ALSO
  auto-translates two mechanical dialect-isms — translate_dialect() (unit-tested):
  datetime('now') [no-modifier form] -> now(), and INSERT OR IGNORE -> INSERT … ON
  CONFLICT DO NOTHING. The psycopg wiring itself still needs exercising against a LIVE
  Postgres before a production cutover. What REMAINS as per-site ports (no mechanical
  rewrite — must be done + validated against a live Postgres): datetime('now', <mods>)
  (interval math, e.g. now() - interval '1 day'), INSERT OR REPLACE (needs ON
  CONFLICT(<target>) DO UPDATE SET), and the json_object audit triggers (a PG trigger
  function). Treat this file as the migration scaffold + paramstyle/dialect layer, not
  yet a drop-in switch. It ships
  SQLite-active and import-guarded so nothing breaks until you opt in. See
  docs/SCALING.md for the full horizontal-scaling plan and remaining blockers.
"""
import os, sqlite3

WORKDIR = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.environ.get("DB_ENGINE", "sqlite")

# Engine-portable exception aliases. Handlers across the codebase that catch
# sqlite3.OperationalError / sqlite3.IntegrityError should catch these instead so
# the SAME `except` clause also catches the equivalent psycopg classes when running
# on Postgres. On SQLite-only installs (the default — psycopg absent) these are just
# the sqlite3 classes, so behavior is byte-identical. Always tuples so `except` works.
DBError = (sqlite3.OperationalError,)         # connection/lock/operational failures
IntegrityError = (sqlite3.IntegrityError,)    # constraint / unique-key violations
try:                                          # extend with psycopg if it is installed
    import psycopg
    DBError = DBError + (psycopg.OperationalError, psycopg.Error)
    IntegrityError = IntegrityError + (psycopg.errors.IntegrityError,)
except Exception:                             # psycopg absent (SQLite-only) — fine
    pass

# SQLite stores a type *affinity*; map each to the closest Postgres type so the
# migrated schema isn't an all-TEXT blob that loses numeric ordering/sums.
def _pg_type(sqlite_decl):
    d = (sqlite_decl or "").upper()
    if "INT" in d:                                   return "bigint"
    if any(k in d for k in ("REAL", "FLOA", "DOUB")): return "double precision"
    if "BLOB" in d:                                  return "bytea"
    return "text"                                     # TEXT, NUMERIC, dates, default


def qmark_to_pyformat(sql):
    """Translate SQLite qmark placeholders (?) to psycopg pyformat (%s) so the SAME
    module SQL runs on Postgres unchanged. A '?' inside a single-quoted string literal
    is left alone, and any literal '%' is doubled to '%%' (psycopg's pyformat treats %
    specially). This is the paramstyle shim the HONEST STATUS note above called for.

    Pure function — fully unit-tested independent of any live database."""
    out, in_str, i, n = [], False, 0, len(sql)
    while i < n:
        c = sql[i]
        if in_str:
            # psycopg scans the WHOLE query for %, so a literal % must be doubled even
            # inside a string literal; ? inside a literal is data and is left alone.
            out.append("%%" if c == "%" else c)
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":   # '' = escaped quote, still inside
                    out.append("'"); i += 2; continue
                in_str = False
            i += 1; continue
        if c == "'":
            in_str = True; out.append(c)
        elif c == "?":
            out.append("%s")
        elif c == "%":
            out.append("%%")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def translate_dialect(sql):
    """Mechanically translate the two SQLite dialect-isms that map to Postgres with a
    SAFE, lossless string rewrite, so the modules' SQLite-style SQL runs on Postgres
    without editing each call site. String-literal-aware (mirrors qmark_to_pyformat):
    a match INSIDE a single-quoted literal is data and is left untouched, honoring ''
    escapes. Applied ONLY on the Postgres path (the shim), so SQLite is never affected.

    Translations (and DELIBERATE non-translations):
      * datetime('now')  -> now()   — ONLY the exact no-modifier form. The modifier
        form datetime('now', '-1 day') needs PG interval math (now() - interval
        '1 day'), which is NOT a mechanical rewrite, so it is LEFT UNCHANGED to be
        caught/ported at live-PG validation.
      * INSERT OR IGNORE INTO -> INSERT INTO ... ON CONFLICT DO NOTHING (appended).
        Case-insensitive on the keywords. The ON CONFLICT clause is inserted before a
        trailing RETURNING / ';' if present (the codebase's forms are simple
        INSERT OR IGNORE INTO ... VALUES (...), all verified by grep).
      * INSERT OR REPLACE is LEFT UNCHANGED — it needs an explicit conflict target +
        DO UPDATE SET, not a mechanical rewrite. CURRENT_TIMESTAMP is also untouched
        (valid standard SQL on Postgres).

    Pure function — fully unit-tested independent of any live database."""
    # Pass 1: datetime('now') -> now(), literal-aware, no-modifier form only.
    out, in_str, i, n = [], False, 0, len(sql)
    low = sql.lower()
    NOW = "datetime('now')"
    while i < n:
        c = sql[i]
        if in_str:
            out.append(c)
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":   # '' = escaped quote, stay inside
                    out.append("'"); i += 2; continue
                in_str = False
            i += 1; continue
        if c == "'":
            in_str = True; out.append(c); i += 1; continue
        if low.startswith(NOW, i):
            # Only rewrite the bare no-modifier form. Peek past the literal for a comma
            # BEFORE the closing ')': datetime('now', ...) is the modifier form and must
            # be left for a per-site interval port, so don't match it here.
            out.append("now()"); i += len(NOW); continue
        out.append(c); i += 1
    sql = "".join(out)

    # Pass 2: INSERT OR IGNORE INTO -> INSERT INTO + ON CONFLICT DO NOTHING, literal-aware.
    low = sql.lower()
    kw = "insert or ignore into"
    in_str, i, n = False, 0, len(sql)
    while i < n:
        c = sql[i]
        if in_str:
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    i += 2; continue
                in_str = False
            i += 1; continue
        if c == "'":
            in_str = True; i += 1; continue
        if low.startswith(kw, i):
            head = sql[:i] + "INSERT INTO" + sql[i + len(kw):]
            return _append_on_conflict(head)
        i += 1
    return sql


def _append_on_conflict(sql):
    """Append ' ON CONFLICT DO NOTHING' to an INSERT, placing it BEFORE a trailing
    RETURNING clause or ';' if one is present (literal-aware split). The codebase's
    INSERT OR IGNORE forms have neither, but this keeps the rewrite correct if one is
    added later."""
    clause = " ON CONFLICT DO NOTHING"
    low, in_str, i, n = sql.lower(), False, 0, len(sql)
    while i < n:
        c = sql[i]
        if in_str:
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    i += 2; continue
                in_str = False
            i += 1; continue
        if c == "'":
            in_str = True; i += 1; continue
        if c == ";":
            return sql[:i] + clause + sql[i:]
        if low.startswith("returning", i) and (i == 0 or not sql[i - 1].isalnum()):
            return sql[:i].rstrip() + clause + " " + sql[i:]
        i += 1
    return sql.rstrip() + clause


class _PgCursor:
    """psycopg cursor wrapper that translates SQLite-dialect SQL to Postgres: first the
    mechanical dialect-isms (translate_dialect), then qmark -> pyformat. Order matters:
    translate_dialect introduces no '?' or '%', so running it first is safe."""
    def __init__(self, cur): object.__setattr__(self, "_cur", cur)
    def execute(self, sql, params=()):
        self._cur.execute(qmark_to_pyformat(translate_dialect(sql)), params); return self._cur
    def executemany(self, sql, params):
        self._cur.executemany(qmark_to_pyformat(translate_dialect(sql)), params); return self._cur
    def __iter__(self): return iter(self._cur)
    def __getattr__(self, name): return getattr(self._cur, name)


class _PgShim:
    """Wraps a psycopg connection so module SQL written in SQLite's qmark style runs
    unchanged on Postgres: it rewrites ? -> %s on execute/executemany, on both the
    connection and the cursors it hands out. Active ONLY when DB_ENGINE=postgres — the
    SQLite default path never sees this class.

    NOTE: the ?->%s translation AND the mechanical dialect translation (translate_dialect:
    datetime('now')->now(), INSERT OR IGNORE->ON CONFLICT DO NOTHING) are unit-tested; the
    psycopg wiring here is verified by construction and must be exercised against a live
    Postgres before a production cutover (see docs/SCALING.md). Non-mechanical dialect-isms
    (datetime('now', <mods>), INSERT OR REPLACE, json_object triggers) remain per-site
    ports, still open."""
    def __init__(self, con): object.__setattr__(self, "_con", con)
    def execute(self, sql, params=()):
        return _PgCursor(self._con.cursor()).execute(sql, params)
    def cursor(self, *a, **k):
        return _PgCursor(self._con.cursor(*a, **k))
    def __getattr__(self, name):          # commit/rollback/close/etc. pass through
        return getattr(self._con, name)


def connect(name):
    """name: logical db ('customers','suppliers','fuel_history','security')."""
    if ENGINE == "postgres":
        import psycopg
        from psycopg.rows import dict_row
        # dict_row gives row["col"] access, matching sqlite3.Row so callers that
        # index rows by column name keep working unchanged.
        con = psycopg.connect(os.environ["DB_DSN"], autocommit=False, row_factory=dict_row)
        con.execute(f"SET search_path TO {name}, public")
        return _PgShim(con)            # qmark -> pyformat on every statement
    con = sqlite3.connect(f"{WORKDIR}/{name}.db")
    con.row_factory = sqlite3.Row
    return con


def migrate_sqlite_to_postgres(names=("customers","suppliers","fuel_history","security")):
    """Copy every table from each .db file into the matching postgres schema,
    preserving column types (so numbers stay numeric)."""
    import psycopg
    pg = psycopg.connect(os.environ["DB_DSN"], autocommit=True)
    for name in names:
        path = f"{WORKDIR}/{name}.db"
        if not os.path.exists(path):
            continue
        lite = sqlite3.connect(path); lite.row_factory = sqlite3.Row
        pg.execute(f"CREATE SCHEMA IF NOT EXISTS {name}")
        tables = [r[0] for r in lite.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            info = lite.execute(f"PRAGMA table_info({t})").fetchall()
            cols = [c[1] for c in info]
            coldef = ", ".join(f'"{c[1]}" {_pg_type(c[2])}' for c in info)
            pg.execute(f'CREATE TABLE IF NOT EXISTS {name}."{t}" ({coldef})')
            rows = lite.execute(f"SELECT * FROM {t}").fetchall()
            if rows:
                ph = ",".join(["%s"] * len(cols))
                with pg.cursor() as cur:
                    cur.executemany(
                        f'INSERT INTO {name}."{t}" VALUES ({ph})',
                        [tuple(r) for r in rows])
            print(f"  {name}.{t}: {len(rows)} rows")
        lite.close()
    pg.close()
    print("migration complete - see the HONEST STATUS note before flipping DB_ENGINE")


if __name__ == "__main__":
    import sys
    if "--migrate" in sys.argv:
        migrate_sqlite_to_postgres()
    else:
        print(f"DB_ENGINE={ENGINE}")
        for n in ("customers","suppliers","fuel_history","security"):
            try:
                c = connect(n); c.close(); print(f"  {n}: OK")
            except Exception as e:
                print(f"  {n}: {e}")
