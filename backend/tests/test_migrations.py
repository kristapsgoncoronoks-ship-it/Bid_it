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
def test_wo8_fx_data_migration_corrects_multiplied_amounts(tmp_path):
    """WO-8: the data migration (b1c3e5a7f9d1) maps free-text fx_source through
    the explicit table, corrects amounts produced by the old original×rate
    multiply ON UNDECIDED reports (divide is the one convention), leaves DECIDED
    reports untouched (flagged — a business decision, docs/DECISIONS-NEEDED.md),
    and closes fx_source behind a CHECK constraint."""
    db_file = tmp_path / "wo8.db"
    async_url = f"sqlite+aiosqlite:///{db_file}"

    up = _run_alembic(["upgrade", "c2d4f6a8b0e3"], async_url)  # the pre-WO-8 head
    assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"

    eng = create_engine(f"sqlite:///{db_file}")
    with eng.begin() as con:
        con.exec_driver_sql(
            "INSERT INTO organizations (id, name, ai_validation_enabled, "
            "human_validation_enabled, plan, status, region) "
            "VALUES ('org1', 'O', 0, 0, 'trial', 'active', 'eu')"
        )

        def report(rid, status, total):
            con.exec_driver_sql(
                "INSERT INTO expense_reports (id, org_id, employee_id, employee_name,"
                " title, status, currency, total, vat_total) VALUES "
                f"('{rid}', 'org1', 'u1', 'E', 'T', '{status}', 'EUR', {total}, 0)"
            )

        def item(iid, rid, amount, orig, fx_rate, fx_source):
            con.exec_driver_sql(
                "INSERT INTO expense_items (id, org_id, report_id, spend_date, category,"
                " description, merchant, amount, currency, original_amount, fx_rate,"
                " fx_source, vat_amount, reclaimable_tax, payment_method,"
                " customer_billable, expense_type) VALUES "
                f"('{iid}', 'org1', '{rid}', '2026-05-01', 'travel', 'X', NULL,"
                f" {amount}, 'USD', {orig}, {fx_rate}, '{fx_source}', 0, 1,"
                " 'personal', 0, 'standard')"
            )

        # Draft report: multiplied amount (100 × 1.23456 = 123.46) → corrected.
        report("r1", "draft", "123.46")
        item("i1", "r1", "123.46", "100.00", "1.23456", "ECB")
        # Approved report: same defect → FLAGGED, never restated by a migration.
        report("r2", "approved", "123.46")
        item("i2", "r2", "123.46", "100.00", "1.23456", "manual")
        # Unmappable free text → 'unknown' (never a guess).
        report("r3", "draft", "50.00")
        item("i3", "r3", "50.00", "40.00", "0.80", "totally-custom")

    up2 = _run_alembic(["upgrade", "head"], async_url)
    assert up2.returncode == 0, f"{up2.stdout}\n{up2.stderr}"
    # The migration PRINTS its reconciliation report (old → new, per bucket).
    assert "[WO-8]" in up2.stdout
    assert "corrected item i1" in up2.stdout
    assert "FLAGGED item i2" in up2.stdout

    with eng.begin() as con:
        rows = {
            r[0]: r
            for r in con.exec_driver_sql(
                "SELECT id, amount, fx_source FROM expense_items"
            ).fetchall()
        }
        # Corrected to the divide convention: 100 / 1.23456 = 81.00.
        assert float(rows["i1"][1]) == 81.00
        assert rows["i1"][2] == "ecb"  # free text mapped through the table
        # Decided report untouched (the wrong 123.46 stays until the business
        # decides — see docs/DECISIONS-NEEDED.md), provenance still mapped.
        assert float(rows["i2"][1]) == 123.46
        assert rows["i2"][2] == "stated"  # 'manual' → stated
        # 40 / 0.80 = 50.00 == stored → NOT a multiply artefact; only the
        # unmappable provenance changes.
        assert float(rows["i3"][1]) == 50.00
        assert rows["i3"][2] == "unknown"
        # The corrected report's total was recomputed from its items.
        totals = {
            r[0]: float(r[1])
            for r in con.exec_driver_sql("SELECT id, total FROM expense_reports").fetchall()
        }
        assert totals["r1"] == 81.00
        assert totals["r2"] == 123.46

        # The enum is now CLOSED: free text is refused by the CHECK constraint.
        import sqlalchemy.exc

        try:
            con.exec_driver_sql("UPDATE expense_items SET fx_source = 'banana' WHERE id = 'i1'")
            raise AssertionError("free-text fx_source must be refused after WO-8")
        except sqlalchemy.exc.IntegrityError:
            pass
    eng.dispose()


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
