"""Foundation guard: Alembic migrations are the production schema authority
(create_all is dev/CI only). These tests keep migrations honest:

1. Migrations apply cleanly from an EMPTY database, and downgrade→upgrade
   round-trips — so a broken or non-reversible migration fails the build.
2. The schema produced by running all migrations MATCHES the schema the ORM
   models declare (table + column parity) — so a model change that ships without
   a matching migration is caught in CI, not discovered in production.

Portability note: we compare two databases we both build (models via create_all
vs. migrations via alembic) with the SQLAlchemy inspector, rather than relying on
Alembic autogenerate reflection (which is noisy on SQLite). This is deterministic
and dialect-robust for the table/column parity that matters most.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Tables that legitimately exist in one DB but not the other.
_IGNORE_TABLES = {"alembic_version"}


def _run_alembic(args: list[str], db_url_async: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": db_url_async}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _schema(sync_url: str) -> dict[str, set[str]]:
    """{table_name: {column_names}} for a built database."""
    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        out: dict[str, set[str]] = {}
        for table in insp.get_table_names():
            if table in _IGNORE_TABLES:
                continue
            out[table] = {c["name"] for c in insp.get_columns(table)}
        return out
    finally:
        engine.dispose()


@pytest.mark.slow
def test_migrations_apply_and_roundtrip_from_empty(tmp_path):
    db_file = tmp_path / "mig.db"
    async_url = f"sqlite+aiosqlite:///{db_file}"

    up = _run_alembic(["upgrade", "head"], async_url)
    assert up.returncode == 0, f"alembic upgrade head failed:\n{up.stdout}\n{up.stderr}"

    # Full reversibility: unwind every migration, then re-apply.
    down = _run_alembic(["downgrade", "base"], async_url)
    assert down.returncode == 0, f"alembic downgrade base failed:\n{down.stdout}\n{down.stderr}"
    reup = _run_alembic(["upgrade", "head"], async_url)
    assert reup.returncode == 0, f"alembic re-upgrade failed:\n{reup.stdout}\n{reup.stderr}"


@pytest.mark.slow
def test_models_match_migration_head(tmp_path):
    # DB A: what the ORM models declare.
    models_file = tmp_path / "models.db"
    eng = create_engine(f"sqlite:///{models_file}")
    import app.models  # noqa: F401
    from app.models.base import Base

    Base.metadata.create_all(eng)
    eng.dispose()
    models_schema = _schema(f"sqlite:///{models_file}")

    # DB B: what the migrations produce.
    mig_file = tmp_path / "migrated.db"
    res = _run_alembic(["upgrade", "head"], f"sqlite+aiosqlite:///{mig_file}")
    assert res.returncode == 0, f"alembic upgrade head failed:\n{res.stdout}\n{res.stderr}"
    migrated_schema = _schema(f"sqlite:///{mig_file}")

    # Table parity.
    only_models = set(models_schema) - set(migrated_schema)
    only_migrations = set(migrated_schema) - set(models_schema)
    assert not only_models, (
        f"Tables declared by models but NOT created by any migration: {sorted(only_models)}. "
        "Generate a migration for the new model(s)."
    )
    assert not only_migrations, (
        f"Tables created by migrations but not present in the models: {sorted(only_migrations)}. "
        "Update the models or add a migration to drop the stale table."
    )

    # Column parity per shared table.
    mismatches: dict[str, dict] = {}
    for table in sorted(set(models_schema) & set(migrated_schema)):
        missing_in_migrations = models_schema[table] - migrated_schema[table]
        missing_in_models = migrated_schema[table] - models_schema[table]
        if missing_in_migrations or missing_in_models:
            mismatches[table] = {
                "declared_by_models_but_not_migrated": sorted(missing_in_migrations),
                "migrated_but_not_in_models": sorted(missing_in_models),
            }
    assert not mismatches, (
        "Model/migration column drift detected — add a migration to reconcile:\n"
        + "\n".join(f"  {t}: {d}" for t, d in mismatches.items())
    )
