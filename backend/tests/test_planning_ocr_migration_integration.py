"""Combined graph upgrades, record preservation and rollback boundaries.

PostgreSQL uses disposable databases created only when an explicit CI admin URL
is supplied. Application migrations and assertions use the non-superuser role.
No test points a destructive reset at RLS_TEST_DATABASE_URL.
"""

import asyncio
import importlib.util
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.test_migrations import BACKEND_DIR, _run_alembic

MERGE = "e5f7091a2345"
PARENTS = {"c3d5e7f90112", "d4e6f8a01223"}
MAIN = "c129fa806b32"


@pytest.fixture(params=["sqlite", "postgres"])
def migration_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite+aiosqlite:///{tmp_path / 'integration.db'}"
        return
    admin_url = os.environ.get("MIGRATION_TEST_ADMIN_URL")
    app_url = os.environ.get("RLS_TEST_DATABASE_URL")
    if not admin_url or not app_url:
        pytest.skip("requires explicit disposable-database CI admin and app URLs")
    import asyncpg

    name = "migration_integration_" + uuid.uuid4().hex
    app = make_url(app_url)
    # Restrict SQL identifiers rather than interpolating arbitrary configuration.
    assert app.username and app.username.replace("_", "").isalnum()

    async def manage(create):
        admin = await asyncpg.connect(admin_url)
        try:
            if create:
                await admin.execute(f'CREATE DATABASE "{name}" OWNER "{app.username}"')
            else:
                await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            await admin.close()

    asyncio.run(manage(True))
    try:
        yield app.set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(manage(False))


def migrate(url, *args):
    result = _run_alembic(list(args), url)
    assert result.returncode == 0, result.stdout + result.stderr


def database(url, action):
    async def run():
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                return await connection.run_sync(action)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def seed(connection):
    from app.models import Base

    org = str(uuid.uuid4())
    project = str(uuid.uuid4())
    run = str(uuid.uuid4())
    connection.execute(
        Base.metadata.tables["organizations"].insert(), {"id": org, "name": "Synthetic"}
    )
    connection.execute(
        Base.metadata.tables["projects"].insert(),
        {"id": project, "org_id": org, "code": "SYNTHETIC", "name": "Synthetic"},
    )
    connection.execute(
        Base.metadata.tables["extraction_runs"].insert(),
        {"id": run, "org_id": org, "method": "text-layer", "note": "retain original evidence"},
    )
    assignment = Table("project_assignments", MetaData(), autoload_with=connection)
    connection.execute(
        assignment.insert(),
        {
            "id": str(uuid.uuid4()),
            "org_id": org,
            "project_id": project,
            "assignee_user_id": str(uuid.uuid4()),
            "assignee_email": "synthetic@example.com",
            "starts_at": datetime(2026, 10, 21, 8, tzinfo=UTC),
            "ends_at": datetime(2026, 10, 21, 10, tzinfo=UTC),
            "created_by": "synthetic@example.com",
        },
    )


def snapshot(connection):
    return {
        name: [dict(row) for row in connection.execute(text(f"SELECT * FROM {name}")).mappings()]
        for name in ("organizations", "projects", "extraction_runs", "project_assignments")
    }


def assert_postgres_schema_matches_contract(connection):
    from app.models import Base

    # DB-017/018 deliberately use PostgreSQL 15+ column-specific SET NULL.
    # Portable ORM metadata says plain SET NULL because SQLite cannot compile
    # the column list. Compare against a COPY carrying the actual PG contract;
    # do not ignore FK diffs or mutate the application's shared metadata.
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    contracts = {
        ("expense_items", "fk_expense_items_project"): "project_id",
        ("invoices", "fk_invoices_project"): "project_id",
        ("issued_invoices", "fk_issued_invoices_project"): "project_id",
        ("issued_invoices", "fk_issued_invoices_issuer"): "issuer_id",
    }
    inspector = inspect(connection)
    for (table, name), column in contracts.items():
        expected = f"SET NULL ({column})"
        actual = next(f for f in inspector.get_foreign_keys(table) if f["name"] == name)
        assert actual["options"]["ondelete"] == expected
        constraint = next(
            f for f in metadata.tables[table].foreign_key_constraints if f.name == name
        )
        constraint.ondelete = expected
        for foreign_key in constraint.elements:
            foreign_key.ondelete = expected
    differences = compare_metadata(MigrationContext.configure(connection), metadata)
    assert not differences, differences


def test_complete_graph_has_one_merge_head_and_no_missing_dependencies():
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    graph = ScriptDirectory.from_config(config)
    assert graph.get_heads() == [MERGE]
    assert set(graph.get_revision(MERGE).down_revision) == PARENTS
    revisions = list(graph.walk_revisions())
    assert len({r.revision for r in revisions}) == len(revisions)
    assert graph.get_revision(MAIN)
    # walk_revisions resolves all down_revision and depends_on edges, not just
    # the three feature scripts named in the report.
    assert all(graph.get_revision(r.revision) for r in revisions)


@pytest.mark.parametrize("start", [None, MAIN, *sorted(PARENTS)])
def test_upgrade_and_marker_roundtrip_preserve_records(migration_url, start):
    before = None
    if start:
        migrate(migration_url, "upgrade", start)
        database(migration_url, seed)
        before = database(migration_url, snapshot)
    migrate(migration_url, "upgrade", "head")
    after = database(migration_url, snapshot)
    if before:
        for table, rows in before.items():
            for old, new in zip(rows, after[table], strict=True):
                assert all(new[key] == value for key, value in old.items())
        assignment = after["project_assignments"][0]
        assert assignment["time_zone"] == "UTC" and assignment["revision"] == 1
        assert assignment["notify_assignee"] and assignment["reminders_enabled"]
        assert assignment["client_notice_enabled"] is None
    if make_url(migration_url).get_backend_name() == "postgresql":
        database(migration_url, assert_postgres_schema_matches_contract)
    else:
        migrate(migration_url, "check")

    def schemas(connection):
        inspector = inspect(connection)
        return {
            name: [
                (c["name"], str(c["type"]), c["nullable"], c["default"])
                for c in inspector.get_columns(name)
            ]
            for name in inspector.get_table_names()
            if name != "alembic_version"
        }

    schema_before = database(migration_url, schemas)
    # An unqualified relative downgrade is ambiguous at a merge point.
    refused = _run_alembic(["downgrade", "-1"], migration_url)
    assert refused.returncode != 0 and "Ambiguous walk" in refused.stdout + refused.stderr
    # An explicit parent removes only the marker and exposes BOTH parents.
    migrate(migration_url, "downgrade", "c3d5e7f90112")
    versions = database(
        migration_url,
        lambda c: set(c.execute(text("SELECT version_num FROM alembic_version")).scalars()),
    )
    assert versions == PARENTS
    assert database(migration_url, schemas) == schema_before
    assert database(migration_url, snapshot) == after
    migrate(migration_url, "upgrade", "head")
    assert database(migration_url, snapshot) == after


def test_downgrade_refuses_changed_assignments_and_applied_ocr(migration_url):
    migrate(migration_url, "upgrade", MAIN)
    database(migration_url, seed)
    migrate(migration_url, "upgrade", "head")

    def guards(connection):
        connection.execute(text("UPDATE project_assignments SET revision=2, notify_assignee=false"))
        run, org = connection.execute(text("SELECT id, org_id FROM extraction_runs")).one()
        from app.models import Base

        connection.execute(
            Base.metadata.tables["capture_region_reads"].insert(),
            {
                "org_id": org,
                "extraction_run_id": run,
                "command_key": str(uuid.uuid4()),
                "payload_hash": "a" * 64,
                "source_sha256": "b" * 64,
                "attempt_seq": 1,
                "request_json": "{}",
                "applied_at": datetime.now(UTC),
                "before_draft_json": '{"supplier_name":"original"}',
            },
        )
        for filename, message in (
            ("b2c4d6e8f010_assignment_delivery_profiles.py", "retain the additive schema"),
            ("d4e6f8a01223_capture_region_reads.py", "Applied region evidence exists"),
        ):
            path = Path(BACKEND_DIR) / "alembic/versions" / filename
            spec = importlib.util.spec_from_file_location("guard", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match=message):
                    module.downgrade()
        assert connection.scalar(text("SELECT revision FROM project_assignments")) == 2
        assert connection.scalar(text("SELECT before_draft_json FROM capture_region_reads")) == (
            '{"supplier_name":"original"}'
        )

    database(migration_url, guards)
