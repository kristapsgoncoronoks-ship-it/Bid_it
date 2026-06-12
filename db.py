"""
DATABASE ABSTRACTION - single place that opens connections, so the whole system
can move from SQLite (default) to PostgreSQL by changing one environment variable,
without editing every module.

    DB_ENGINE=sqlite      (default) - file-based, zero setup, ideal single-server
    DB_ENGINE=postgres    - set DB_DSN=postgresql://user:pass@host/dbname

How modules use it: customer_db / supplier_db / vat_refund call db.connect(name).
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
  Postgres types on migration, and returns dict-style rows so `row["col"]` keeps
  working. What it does NOT do yet: the modules write SQL with SQLite's qmark
  paramstyle ("... VALUES (?)") and a few SQLite-isms (datetime('now'),
  INSERT OR IGNORE, json_object in audit triggers). psycopg uses the pyformat
  paramstyle ("%s") and Postgres spells those functions differently, so a real
  cutover still needs a paramstyle shim (e.g. wrap connect() to translate ? -> %s)
  and dialect-aware audit/migration DDL. Treat this file as the migration
  scaffold, not a drop-in switch. It ships SQLite-active and import-guarded so
  nothing breaks until you opt in.
"""
import os, sqlite3

WORKDIR = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.environ.get("DB_ENGINE", "sqlite")

# SQLite stores a type *affinity*; map each to the closest Postgres type so the
# migrated schema isn't an all-TEXT blob that loses numeric ordering/sums.
def _pg_type(sqlite_decl):
    d = (sqlite_decl or "").upper()
    if "INT" in d:                                   return "bigint"
    if any(k in d for k in ("REAL", "FLOA", "DOUB")): return "double precision"
    if "BLOB" in d:                                  return "bytea"
    return "text"                                     # TEXT, NUMERIC, dates, default


def connect(name):
    """name: logical db ('customers','suppliers','fuel_history','security')."""
    if ENGINE == "postgres":
        import psycopg
        from psycopg.rows import dict_row
        # dict_row gives row["col"] access, matching sqlite3.Row so callers that
        # index rows by column name keep working unchanged.
        con = psycopg.connect(os.environ["DB_DSN"], autocommit=False, row_factory=dict_row)
        con.execute(f"SET search_path TO {name}, public")
        return con
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
