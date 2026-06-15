"""
Unit tests for db.translate_dialect — the mechanical SQLite->Postgres dialect
rewriter used ONLY on the Postgres path (the _PgShim cursor). Like qmark_to_pyformat,
it is a pure, string-literal-aware function, so these run on the default SQLite engine
with no live Postgres required (and no psycopg needed — only db.py is imported).

It performs exactly two SAFE, mechanical translations and deliberately leaves the
non-mechanical dialect-isms alone (datetime('now', <mods>), INSERT OR REPLACE,
CURRENT_TIMESTAMP) for a per-site port validated against a live Postgres.
"""
import db


# ----------------------------------------------------- datetime('now') -> now()
def test_datetime_now_basic():
    assert db.translate_dialect("SELECT datetime('now')") == "SELECT now()"


def test_datetime_now_multiple_occurrences():
    sql = "SELECT datetime('now'), datetime('now')"
    assert db.translate_dialect(sql) == "SELECT now(), now()"


def test_datetime_now_embedded_in_statement():
    sql = "INSERT INTO t (a, ts) VALUES (?, datetime('now'))"
    assert db.translate_dialect(sql) == "INSERT INTO t (a, ts) VALUES (?, now())"


def test_datetime_now_inside_string_literal_not_translated():
    # the text datetime('now') sitting INSIDE a quoted literal is data, not a call
    sql = "SELECT 'datetime(''now'')' AS note, datetime('now')"
    assert db.translate_dialect(sql) == "SELECT 'datetime(''now'')' AS note, now()"


def test_datetime_now_with_modifier_left_unchanged():
    # modifier form needs PG interval math (now() - interval '1 day') — NOT mechanical
    sql = "SELECT * FROM t WHERE ts >= datetime('now','-1 day')"
    assert db.translate_dialect(sql) == sql


def test_datetime_now_with_param_modifier_left_unchanged():
    # the real codebase form: datetime('now', ?)
    sql = "SELECT COUNT(*) FROM t WHERE ts >= datetime('now', ?)"
    assert db.translate_dialect(sql) == sql


# --------------------------------------- INSERT OR IGNORE -> ON CONFLICT DO NOTHING
def test_insert_or_ignore_basic():
    sql = "INSERT OR IGNORE INTO t (a) VALUES (?)"
    assert db.translate_dialect(sql) == \
        "INSERT INTO t (a) VALUES (?) ON CONFLICT DO NOTHING"


def test_insert_or_ignore_case_insensitive():
    # match is case-insensitive; the keyword is normalized to canonical "INSERT INTO"
    sql = "insert or ignore into t (a) values (?)"
    assert db.translate_dialect(sql) == \
        "INSERT INTO t (a) values (?) ON CONFLICT DO NOTHING"


def test_insert_or_ignore_mixed_case():
    sql = "Insert Or Ignore Into supplier_rate_state (supplier) VALUES (?)"
    assert db.translate_dialect(sql) == \
        "INSERT INTO supplier_rate_state (supplier) VALUES (?) ON CONFLICT DO NOTHING"


def test_insert_or_ignore_before_trailing_semicolon():
    sql = "INSERT OR IGNORE INTO t (a) VALUES (?);"
    assert db.translate_dialect(sql) == \
        "INSERT INTO t (a) VALUES (?) ON CONFLICT DO NOTHING;"


def test_insert_or_ignore_before_returning():
    sql = "INSERT OR IGNORE INTO t (a) VALUES (?) RETURNING id"
    assert db.translate_dialect(sql) == \
        "INSERT INTO t (a) VALUES (?) ON CONFLICT DO NOTHING RETURNING id"


# ----------------------------------------------- deliberate non-translations
def test_insert_or_replace_left_unchanged():
    # INSERT OR REPLACE needs an explicit conflict target + DO UPDATE SET — not mechanical
    sql = "INSERT OR REPLACE INTO users (username, salt) VALUES (?, ?)"
    assert db.translate_dialect(sql) == sql


def test_current_timestamp_left_unchanged():
    sql = "INSERT INTO t (ts) VALUES (CURRENT_TIMESTAMP)"
    assert db.translate_dialect(sql) == sql


def test_unrelated_statement_byte_identical():
    sql = "SELECT a, b FROM t WHERE c = ? ORDER BY a"
    assert db.translate_dialect(sql) is not None
    assert db.translate_dialect(sql) == sql


# ----------------------------------------------- composition with qmark_to_pyformat
def test_composition_translate_then_pyformat():
    # the _PgCursor order: translate_dialect first, then qmark_to_pyformat
    sql = "INSERT OR IGNORE INTO t (a, b) VALUES (?, ?)"
    out = db.qmark_to_pyformat(db.translate_dialect(sql))
    assert out == "INSERT INTO t (a, b) VALUES (%s, %s) ON CONFLICT DO NOTHING"


def test_composition_preserves_literal_percent_doubling():
    # a literal % in a LIKE pattern must still be doubled by pyformat after dialect rewrite
    sql = "INSERT OR IGNORE INTO t (a) SELECT a FROM s WHERE a LIKE '%x%'"
    out = db.qmark_to_pyformat(db.translate_dialect(sql))
    assert out == \
        "INSERT INTO t (a) SELECT a FROM s WHERE a LIKE '%%x%%' ON CONFLICT DO NOTHING"
