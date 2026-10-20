"""R010 races and committed readback on migrated non-superuser PostgreSQL."""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from app.models.audit import AuditEvent
from app.models.expense import (
    ExpenseCommand,
    ExpenseImportBatch,
    ExpenseItem,
    ExpenseReport,
    ExpenseTransaction,
)

PG_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires migrated PostgreSQL")
CSV = b"Date,Description,Amount,Currency\n2026-10-01,Synthetic hotel,-12.34,USD\n"


@pytest.mark.parametrize("same_key", [True, False])
async def test_import_and_report_races_have_one_financial_outcome(same_key):
    engine = create_async_engine(PG_URL, pool_size=6, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    try:
        async with sessions() as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                    )
                )
                is False
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic R010 concurrency",
                    "name": "Owner",
                    "email": f"r010-{uuid.uuid4().hex}@example.com",
                    "password": "synthetic-test-password",
                },
            )
            assert registration.status_code == 201, registration.text
            org_id = registration.json()["organization"]["id"]
            client.headers["Authorization"] = (
                "Bearer " + registration.json()["token"]["access_token"]
            )
            assert (
                await client.put("/api/v1/modules/expenses", json={"enabled": True})
            ).status_code == 200
            import_key = str(uuid.uuid4())
            imports = await asyncio.gather(
                *[
                    client.post(
                        "/api/v1/expenses/import/bank-statement",
                        files={"file": ("synthetic.csv", CSV, "text/csv")},
                        data={"account_reference": "operating-card", "statement_currency": "USD"},
                        headers={
                            "Idempotency-Key": import_key if same_key else f"{import_key}:{i}"
                        },
                    )
                    for i in range(3)
                ]
            )
            assert [response.status_code for response in imports] == [200, 200, 200], [
                r.text for r in imports
            ]
            assert len({response.json()["batch_id"] for response in imports}) == 1
            assert sum(response.json()["imported"] for response in imports) == 1
            available = await client.get("/api/v1/expenses/transactions")
            rows = available.json()
            assert len(rows) == 1 and rows[0]["currency"] == "USD"
            row = rows[0]
            key = str(uuid.uuid4())
            body = {
                "title": "Synthetic USD report",
                "currency": "USD",
                "transaction_ids": [row["id"]],
                "transaction_versions": {row["id"]: row["version"]},
            }
            reports = await asyncio.gather(
                *[
                    client.post(
                        "/api/v1/expenses",
                        json=body,
                        headers={"Idempotency-Key": key if same_key else f"{key}:{i}"},
                    )
                    for i in range(3)
                ]
            )
            assert sorted(r.status_code for r in reports) == (
                [201, 201, 201] if same_key else [201, 409, 409]
            ), [r.text for r in reports]
            ids = {r.json()["id"] for r in reports if r.status_code == 201}
            assert len(ids) == 1
            report_id = ids.pop()
            readback = await client.get(f"/api/v1/expenses/{report_id}")
            assert readback.status_code == 200
            assert readback.json()["total"] == "12.34"
            assert readback.json()["items"][0]["original_amount"] == "12.34"
            assert readback.json()["items"][0]["currency"] == "USD"
            assert (await client.get("/api/v1/expenses/transactions")).json() == []
            async with sessions() as session:
                for model, count in (
                    (ExpenseImportBatch, 1),
                    (ExpenseTransaction, 1),
                    (ExpenseReport, 1),
                    (ExpenseItem, 1),
                    (ExpenseCommand, 2 if same_key else 4),
                ):
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(model).where(model.org_id == org_id)
                        )
                        == count
                    )
                for action in ("expense.statement_imported", "expense.report_created"):
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(AuditEvent)
                            .where(AuditEvent.org_id == org_id, AuditEvent.action == action)
                        )
                        == 1
                    )
                txn = await session.get(ExpenseTransaction, row["id"])
                assert txn.status == "assigned" and txn.item_id is not None
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def test_receipts_have_database_uniqueness_rls_and_downgrade_refusal(monkeypatch):
    engine = create_async_engine(PG_URL, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic receipt retention",
                    "name": "Owner",
                    "email": f"r010-retention-{uuid.uuid4().hex}@example.com",
                    "password": "synthetic-test-password",
                },
            )
            assert reg.status_code == 201, reg.text
            org_id = reg.json()["organization"]["id"]
            client.headers["Authorization"] = "Bearer " + reg.json()["token"]["access_token"]
            enabled = await client.put("/api/v1/modules/expenses", json={"enabled": True})
            assert enabled.status_code == 200
            imported = await client.post(
                "/api/v1/expenses/import/bank-statement",
                files={"file": ("synthetic.csv", CSV, "text/csv")},
                data={"account_reference": "retention-card", "statement_currency": "USD"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert imported.status_code == 200, imported.text

        async with sessions() as session:
            receipt = await session.scalar(
                select(ExpenseCommand).where(ExpenseCommand.org_id == org_id)
            )
            assert receipt is not None
            with pytest.raises(IntegrityError, match="uq_expense_command_identity"):
                async with session.begin_nested():
                    await session.execute(
                        insert(ExpenseCommand).values(
                            org_id=receipt.org_id,
                            employee_id=receipt.employee_id,
                            command_key=receipt.command_key,
                            operation=receipt.operation,
                            payload_hash=receipt.payload_hash,
                            target_id=receipt.target_id,
                            result_json=receipt.result_json,
                        )
                    )

        # Raw SQL bypasses ORM tenant filters: the database itself must hide both tables.
        async with engine.begin() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                    )
                )
                is False
            )
            await connection.execute(
                text("SELECT set_config('app.current_org', :org, true)"),
                {"org": str(uuid.uuid4())},
            )
            for table in ("expense_commands", "expense_import_batches"):
                assert (
                    await connection.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE org_id = :org"),
                        {"org": org_id},
                    )
                    == 0
                )

        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic/versions/b018e9f4c021_expense_command_receipts.py"
        )
        spec = importlib.util.spec_from_file_location("r010_retention_migration", migration_path)
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        def refuse_destructive_downgrade(connection):
            with monkeypatch.context() as scoped_patch:
                scoped_patch.setattr(migration.op, "get_bind", lambda: connection)
                with pytest.raises(RuntimeError, match="R010 receipt retention refusal"):
                    migration.downgrade()

        async with engine.begin() as connection:
            await connection.run_sync(refuse_destructive_downgrade)
        async with sessions() as session:
            for model in (ExpenseCommand, ExpenseImportBatch, ExpenseTransaction):
                assert (
                    await session.scalar(
                        select(func.count()).select_from(model).where(model.org_id == org_id)
                    )
                    == 1
                )
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()
async def test_assignment_vs_remove_and_old_remove_after_restore():
    engine = create_async_engine(PG_URL, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def get_test_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "organization_name": "Synthetic removal races",
                    "name": "Owner",
                    "email": f"r010-bin-{uuid.uuid4().hex}@example.com",
                    "password": "synthetic-test-password",
                },
            )
            assert reg.status_code == 201, reg.text
            client.headers["Authorization"] = "Bearer " + reg.json()["token"]["access_token"]
            await client.put("/api/v1/modules/expenses", json={"enabled": True})
            imported = await client.post(
                "/api/v1/expenses/import/bank-statement",
                files={"file": ("synthetic.csv", CSV, "text/csv")},
                data={"account_reference": "card", "statement_currency": "USD"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert imported.status_code == 200, imported.text
            row = imported.json()["transactions"][0]
            remove_key = str(uuid.uuid4())
            responses = await asyncio.gather(
                client.post(
                    "/api/v1/expenses",
                    json={
                        "title": "Racing report",
                        "currency": "USD",
                        "transaction_ids": [row["id"]],
                        "transaction_versions": {row["id"]: row["version"]},
                    },
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                ),
                client.delete(
                    f"/api/v1/expenses/transactions/{row['id']}",
                    params={"version": row["version"]},
                    headers={"Idempotency-Key": remove_key},
                ),
            )
            assert sorted(r.status_code for r in responses) in ([201, 409], [204, 409]), [
                r.text for r in responses
            ]
            # Use a different source/account so restore is tested regardless of race winner.
            extra = await client.post(
                "/api/v1/expenses/import/bank-statement",
                files={"file": ("synthetic.csv", CSV, "text/csv")},
                data={"account_reference": "second-card", "statement_currency": "USD"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            item = extra.json()["transactions"][0]
            key = str(uuid.uuid4())
            kwargs = {"params": {"version": item["version"]}, "headers": {"Idempotency-Key": key}}
            assert (
                await client.delete(f"/api/v1/expenses/transactions/{item['id']}", **kwargs)
            ).status_code == 204
            back = await client.post(
                f"/api/v1/invoices/trash/other/expense_transaction/{item['id']}/restore"
            )
            assert back.status_code == 200, back.text
            assert (
                await client.delete(f"/api/v1/expenses/transactions/{item['id']}", **kwargs)
            ).status_code == 204
            rows = (await client.get("/api/v1/expenses/transactions")).json()
            restored = [r for r in rows if r["id"] == item["id"]]
            assert len(restored) == 1
            assert restored[0]["version"] > item["version"]
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()

