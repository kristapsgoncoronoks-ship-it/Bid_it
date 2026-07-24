"""DBAPI connect-arg selection (scale-path): behind PgBouncer transaction pooling
asyncpg's prepared-statement caches must be disabled, or statements prepared on
one server connection break on the next."""

from app.core.database import build_connect_args


def test_sqlite_sets_check_same_thread():
    assert build_connect_args(is_sqlite=True, pgbouncer=False) == {"check_same_thread": False}
    # SQLite path ignores the pgbouncer flag (never applies).
    assert build_connect_args(is_sqlite=True, pgbouncer=True) == {"check_same_thread": False}


def test_postgres_direct_has_no_special_args():
    assert build_connect_args(is_sqlite=False, pgbouncer=False) == {}


def test_postgres_behind_pgbouncer_disables_prepared_statements():
    args = build_connect_args(is_sqlite=False, pgbouncer=True)
    assert args == {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
