"""Legacy schedule choices are explicit, and downgrade cannot erase opt-outs."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


def test_legacy_defaults_preserve_time_and_prevent_destructive_downgrade(tmp_path):
    path = (
        Path(__file__).parents[1] / "alembic/versions/b2c4d6e8f010_assignment_delivery_profiles.py"
    )
    spec = importlib.util.spec_from_file_location("assignment_delivery_profiles", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-assignments.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE project_assignments (id TEXT PRIMARY KEY, starts_at DATETIME NOT NULL, "
            "ends_at DATETIME NOT NULL, reminder_sent_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO project_assignments VALUES "
            "('legacy', '2026-10-21 08:00:00', '2026-10-21 10:00:00', '2026-10-20 08:00:00')"
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            row = connection.exec_driver_sql("SELECT * FROM project_assignments").mappings().one()
            assert row["time_zone"] == "UTC" and row["revision"] == 1
            assert row["notify_assignee"] == row["reminders_enabled"] == 1
            assert row["client_notice_enabled"] is None
            assert row["starts_at"] == "2026-10-21 08:00:00"
            assert row["ends_at"] == "2026-10-21 10:00:00"
            assert row["reminder_sent_at"] == "2026-10-20 08:00:00"
            for choice in (
                "time_zone = 'Europe/Riga'",
                "revision = 2",
                "notify_assignee = false",
                "reminders_enabled = false",
                "client_notice_enabled = false",
            ):
                connection.exec_driver_sql(f"UPDATE project_assignments SET {choice}")
                with pytest.raises(RuntimeError, match="retain the additive schema"):
                    module.downgrade()
                connection.exec_driver_sql(
                    "UPDATE project_assignments SET time_zone='UTC', revision=1, "
                    "notify_assignee=true, reminders_enabled=true, client_notice_enabled=NULL"
                )
            module.downgrade()
            assert {c["name"] for c in inspect(connection).get_columns("project_assignments")} == {
                "id",
                "starts_at",
                "ends_at",
                "reminder_sent_at",
            }
    engine.dispose()
