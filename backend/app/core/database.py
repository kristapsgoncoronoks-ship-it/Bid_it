"""Async SQLAlchemy engine + session factory.

The engine is created once per process. Sessions are handed out per-request by
the `get_db` dependency (see app/api/deps.py) and always closed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def build_connect_args(is_sqlite: bool, pgbouncer: bool) -> dict:
    """DBAPI connect args for the engine.

    - SQLite needs ``check_same_thread=False`` (one handle across the loop).
    - Behind PgBouncer in TRANSACTION pooling mode, asyncpg's prepared statements
      break (a statement prepared on one server connection isn't visible on the
      next), so disable both the asyncpg cache and SQLAlchemy's asyncpg-dialect
      cache — each statement then stands alone.
    """
    if is_sqlite:
        return {"check_same_thread": False}
    if pgbouncer:
        return {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
    return {}


_connect_args = build_connect_args(settings.is_sqlite, settings.db_pgbouncer)

# SQLite uses a single file/in-memory handle (no server-side pool tuning); Postgres
# gets an explicit, bounded connection pool so a replica can't exhaust the server.
_pool_kwargs: dict = (
    {}
    if settings.is_sqlite
    else {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": 1800,  # recycle connections every 30 min (avoid stale sockets)
    }
)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,  # detect dropped connections before use (reliability)
    future=True,
    connect_args=_connect_args,
    **_pool_kwargs,
)

if settings.is_sqlite:
    # SQLite does NOT enforce foreign keys (and therefore ON DELETE CASCADE)
    # unless this pragma is set per-connection. Without it, deleting an
    # organization would orphan its child rows instead of cascading. Postgres
    # enforces FKs natively, so this is SQLite-only.
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_fk_pragma(dbapi_connection, _record):  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
