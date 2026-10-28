"""Native check/autogenerate honor exact PG policy without suppressing drift.

Only explicitly configured disposable databases are created. The normal app URL
is never migrated, reset or modified by these controls.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.core.config import settings
from app.core.migration_schema import POSTGRES_COLUMN_SET_NULL, postgresql_comparison_metadata
from app.models import Base
from tests.test_migrations import BACKEND_DIR, _run_alembic


class PrivateDatabaseURL(str):
    def __repr__(self):
        return repr(make_url(self).render_as_string(hide_password=True))


def database(url, action):
    async def run():
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                return await connection.run_sync(action)
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture(scope="module")
def postgres_contract_url():
    admin_url = os.environ.get("MIGRATION_TEST_ADMIN_URL")
    app_url = os.environ.get("RLS_TEST_DATABASE_URL")
    if not admin_url or not app_url:
        pytest.skip("requires explicit disposable-database admin and non-superuser app URLs")
    import asyncpg

    name = "migration_parity_" + uuid.uuid4().hex
    app = make_url(app_url)
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
    url = app.set(database=name).render_as_string(hide_password=False)
    try:
        upgraded = _run_alembic(["upgrade", "head"], url)
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

        def seed(connection):
            assert (
                connection.scalar(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
                is False
            )
            connection.execute(
                Base.metadata.tables["organizations"].insert(),
                {"id": str(uuid.uuid4()), "name": "Synthetic parity evidence"},
            )

        database(url, seed)
        yield PrivateDatabaseURL(url)
    finally:
        asyncio.run(manage(False))


def metadata_signature(metadata):
    return {
        table.name: {
            "columns": [
                (c.name, str(c.type), c.nullable, str(c.server_default)) for c in table.columns
            ],
            "indexes": sorted(
                (i.name, i.unique, tuple(str(c) for c in i.expressions)) for i in table.indexes
            ),
            "fks": {
                (fk.name, tuple(fk.column_keys), tuple(e.target_fullname for e in fk.elements)): (
                    tuple(fk.column_keys),
                    tuple(e.target_fullname for e in fk.elements),
                    fk.ondelete,
                    tuple(e.ondelete for e in fk.elements),
                )
                for fk in table.foreign_key_constraints
            },
        }
        for table in metadata.sorted_tables
    }


def snapshot(connection):
    inspector = inspect(connection)
    quote = connection.dialect.identifier_preparer.quote
    return {
        "rows": {
            table: list(
                connection.execute(
                    text(
                        f"SELECT row_to_json(t)::text FROM {quote(table)} t ORDER BY row_to_json(t)::text"
                    )
                ).scalars()
            )
            for table in sorted(inspector.get_table_names())
        },
        "constraints": list(
            connection.execute(
                text(
                    "SELECT c.relname, x.conname, pg_get_constraintdef(x.oid) FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,x.conname"
                )
            )
        ),
        "indexes": list(
            connection.execute(
                text(
                    "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname"
                )
            )
        ),
        "columns": list(
            connection.execute(
                text(
                    "SELECT table_name,column_name,data_type,udt_name,is_nullable,column_default,character_maximum_length FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position"
                )
            )
        ),
        "policies": list(
            connection.execute(
                text(
                    "SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname"
                )
            )
        ),
    }


def assert_cli_check(url, *, refused=False, expected=None):
    result = _run_alembic(["check"], url)
    if refused:
        assert result.returncode != 0, result.stdout + result.stderr
        if expected:
            assert expected in result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "No new upgrade operations detected" in result.stdout


def config():
    result = Config(str(BACKEND_DIR / "alembic.ini"))
    result.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # Explicit modern separator preserves the single prepend_sys_path="."
    # entry without relying on Alembic's deprecated legacy fallback.
    result.set_main_option("path_separator", "os")
    assert result.cmd_opts is None
    return result


def test_native_cli_and_programmatic_checks_preserve_every_row_and_catalog(
    postgres_contract_url, monkeypatch
):
    before_metadata = metadata_signature(Base.metadata)
    before_database = database(postgres_contract_url, snapshot)
    assert_cli_check(postgres_contract_url)
    monkeypatch.setattr(settings, "database_url", postgres_contract_url)
    same_config = config()
    command.check(same_config)
    # Reusing Config after check must not leak its comparison-only context into
    # a subsequent real upgrade/downgrade invocation.
    from app.core import migration_schema

    def refuse_comparison(*_args):
        raise AssertionError("upgrade/downgrade must not invoke the comparison helper")

    with monkeypatch.context() as guard:
        guard.setattr(migration_schema, "postgresql_comparison_metadata", refuse_comparison)
        command.downgrade(same_config, "e5f7091a2345")
        command.upgrade(same_config, "head")
    assert metadata_signature(Base.metadata) == before_metadata
    assert database(postgres_contract_url, snapshot) == before_database


def test_comparison_copy_only_changes_four_existing_ondelete_forms_without_writes(
    postgres_contract_url,
):
    original = metadata_signature(Base.metadata)

    def compare(connection):
        statements = []

        def callback(_c, _cursor, statement, *_args):
            statements.append(statement)

        event.listen(connection, "before_cursor_execute", callback)
        try:
            copied = postgresql_comparison_metadata(connection, Base.metadata)
        finally:
            event.remove(connection, "before_cursor_execute", callback)
        assert copied is not Base.metadata
        assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
        return metadata_signature(copied)

    copied = database(postgres_contract_url, compare)
    expected = metadata_signature(Base.metadata)
    for (table, name), column in POSTGRES_COLUMN_SET_NULL.items():
        key = next(k for k in expected[table]["fks"] if k[0] == name)
        columns, targets, ondelete, elements = expected[table]["fks"][key]
        assert ondelete == "SET NULL" and all(value == "SET NULL" for value in elements)
        expected[table]["fks"][key] = (
            columns,
            targets,
            f"SET NULL ({column})",
            tuple(f"SET NULL ({column})" for _ in elements),
        )
    assert copied == expected
    assert metadata_signature(Base.metadata) == original


@pytest.mark.parametrize("control", ["target_fk", "other_fk", "index", "column", "type"])
def test_native_check_refuses_target_policy_and_unrelated_schema_drift(
    postgres_contract_url, control
):
    before = database(postgres_contract_url, snapshot)
    if control in {"target_fk", "other_fk"}:
        name = (
            "fk_expense_items_project" if control == "target_fk" else "fk_expense_items_cost_center"
        )
        definition = database(
            postgres_contract_url,
            lambda c: c.scalar(
                text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=:name"),
                {"name": name},
            ),
        )
        changed = (
            definition.replace("SET NULL (project_id)", "SET NULL (org_id)")
            if control == "target_fk"
            else definition.replace("SET NULL", "CASCADE")
        )
        assert changed != definition
        change = (
            f'ALTER TABLE expense_items DROP CONSTRAINT "{name}", ADD CONSTRAINT "{name}" {changed}'
        )
        restore = f'ALTER TABLE expense_items DROP CONSTRAINT "{name}", ADD CONSTRAINT "{name}" {definition}'
        expected = (
            "DB-PG-PARITY-01" if control == "target_fk" else "New upgrade operations detected"
        )
    elif control == "index":
        change = "CREATE INDEX ix_synthetic_parity_probe ON organizations(name)"
        restore = "DROP INDEX ix_synthetic_parity_probe"
        expected = "remove_index"
    elif control == "column":
        change = "ALTER TABLE organizations ADD COLUMN synthetic_parity_probe TEXT"
        restore = "ALTER TABLE organizations DROP COLUMN synthetic_parity_probe"
        expected = "remove_column"
    else:
        change = "ALTER TABLE organizations ALTER COLUMN name TYPE TEXT"
        restore = "ALTER TABLE organizations ALTER COLUMN name TYPE VARCHAR(200)"
        expected = "modify_type"
    database(postgres_contract_url, lambda c: c.execute(text(change)))
    try:
        assert_cli_check(postgres_contract_url, refused=True, expected=expected)
    finally:
        database(postgres_contract_url, lambda c: c.execute(text(restore)))
    assert database(postgres_contract_url, snapshot) == before
    assert_cli_check(postgres_contract_url)


def test_native_autogenerate_compares_without_creating_a_revision(
    postgres_contract_url, monkeypatch
):
    source = {p.name: p.read_bytes() for p in (Path(BACKEND_DIR) / "alembic/versions").glob("*.py")}
    before = database(postgres_contract_url, snapshot)
    monkeypatch.setattr(settings, "database_url", postgres_contract_url)
    observed = []

    def verify(_context, _revision, directives):
        assert len(directives) == 1 and directives[0].upgrade_ops.is_empty()
        observed.append(True)
        directives.clear()

    assert command.revision(config(), autogenerate=True, process_revision_directives=verify) == []
    assert observed == [True]
    assert {
        p.name: p.read_bytes() for p in (Path(BACKEND_DIR) / "alembic/versions").glob("*.py")
    } == source
    assert database(postgres_contract_url, snapshot) == before


@pytest.mark.parametrize("change", ["constraint", "element"])
def test_changed_portable_orm_policy_is_refused_without_mutating_shared_metadata(
    postgres_contract_url,
    change,
):
    from sqlalchemy import MetaData

    original = metadata_signature(Base.metadata)
    alternate = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(alternate)
    fk = next(
        f
        for f in alternate.tables["expense_items"].foreign_key_constraints
        if f.name == "fk_expense_items_project"
    )
    if change == "constraint":
        fk.ondelete = "CASCADE"
    else:
        fk.elements[0].ondelete = "CASCADE"
    with pytest.raises(RuntimeError, match="portable ORM contract changed"):
        database(postgres_contract_url, lambda c: postgresql_comparison_metadata(c, alternate))
    assert metadata_signature(Base.metadata) == original


def test_database_fixture_representation_redacts_password():
    example = "postgresql+asyncpg://synthetic:synthetic_password@localhost/synthetic"
    wrapped = PrivateDatabaseURL(example)
    assert str(wrapped) == example
    assert "synthetic_password" not in repr(wrapped)
    assert ":***@" in repr(wrapped)
