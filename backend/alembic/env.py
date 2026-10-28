"""Alembic environment — async-aware, wired to app models + settings."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings
from app.core.migration_schema import postgresql_comparison_metadata
from app.models import Base

config = context.config

# Inject the runtime DB URL (env-driven) so we never hardcode credentials.
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=settings.is_sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=settings.is_sqlite,
    )
    # Check/autogenerate compare the migrated database, not an empty upgrade
    # target. RevisionContext is passed by Alembic for CLI and programmatic
    # check/revision commands; ordinary upgrade/downgrade never enter this path.
    revision_context = context.get_context().opts.get("revision_context")
    if (
        connection.dialect.name == "postgresql"
        and revision_context is not None
        and revision_context.command_args.get("autogenerate")
    ):
        comparison = postgresql_comparison_metadata(connection, target_metadata)
        context.configure(connection=connection, target_metadata=comparison, render_as_batch=False)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
