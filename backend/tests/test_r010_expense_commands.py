"""R010 acceptance contracts through the real API and persisted test database.

SQLite characterizes the HTTP contract here; the companion PostgreSQL suite
proves competing connections and schema guarantees.
"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from starlette.datastructures import UploadFile

from app.models.expense import ExpenseReport, ExpenseTransaction
from app.models.user import User

CSV = b"Date,Description,Amount,Currency\n2026-10-01,Synthetic hotel,-12.34,USD\n"


async def activate(client):
    response = await client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert response.status_code == 200, response.text


async def import_statement(client, *, content=CSV, key=None, currency="USD", account="test-card"):
    return await client.post(
        "/api/v1/expenses/import/bank-statement",
        files={"file": ("statement.csv", content, "text/csv")},
        data={"statement_currency": currency, "account_reference": account},
        headers={"Idempotency-Key": key or str(uuid4())},
    )


async def seed_transactions(db, currencies=("EUR",)):
    owner = await db.scalar(select(User).where(User.email == "owner@acme.io"))
    from datetime import date
    from decimal import Decimal

    rows = [
        ExpenseTransaction(
            org_id=owner.org_id,
            employee_id=owner.id,
            txn_date=date(2026, 10, 1),
            description=f"Synthetic {ccy} candidate {i}",
            amount=Decimal("10.00"),
            currency=ccy,
            direction="debit",
            source="card",
            status="available",
        )
        for i, ccy in enumerate(currencies)
    ]
    db.add_all(rows)
    await db.commit()
    return rows


def report_payload(rows, *, currency="EUR"):
    return {
        "title": "Synthetic selected expenses",
        "currency": currency,
        "transaction_ids": [row.id for row in rows],
        "transaction_versions": {row.id: getattr(row, "version", 1) for row in rows},
    }


@pytest.mark.asyncio
async def test_import_preserves_stated_currency(auth_client, db_session):
    await activate(auth_client)
    response = await import_statement(auth_client)
    assert response.status_code == 200, response.text
    assert response.json()["transactions"][0]["currency"] == "USD"
    saved = await db_session.scalar(select(ExpenseTransaction))
    assert saved.currency == "USD"
    assert str(saved.amount) == "12.34"


@pytest.mark.asyncio
async def test_exact_bytes_with_another_key_do_not_duplicate_candidates(auth_client, db_session):
    await activate(auth_client)
    first = await import_statement(auth_client)
    second = await import_statement(auth_client)
    assert first.status_code == second.status_code == 200
    assert second.json()["imported"] == 0
    assert second.json()["batch_id"] == first.json()["batch_id"]
    assert second.json()["replayed"] is True
    assert await db_session.scalar(select(func.count()).select_from(ExpenseTransaction)) == 1


@pytest.mark.asyncio
async def test_reused_import_key_with_different_bytes_conflicts(auth_client, db_session):
    await activate(auth_client)
    key = str(uuid4())
    first = await import_statement(auth_client, key=key)
    second = await import_statement(auth_client, key=key, content=CSV.replace(b"12.34", b"19.87"))
    assert first.status_code == 200
    assert second.status_code == 409, second.text
    assert await db_session.scalar(select(func.count()).select_from(ExpenseTransaction)) == 1


@pytest.mark.asyncio
async def test_unknown_currency_is_refused_before_writing(auth_client, db_session):
    await activate(auth_client)
    response = await auth_client.post(
        "/api/v1/expenses/import/bank-statement",
        files={
            "file": (
                "statement.csv",
                b"Date,Description,Amount\n2026-10-01,Unknown,-10\n",
                "text/csv",
            )
        },
        data={"account_reference": "test-card"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422, response.text
    assert await db_session.scalar(select(func.count()).select_from(ExpenseTransaction)) == 0


@pytest.mark.asyncio
async def test_mixed_currency_selection_refuses_all_writes(auth_client, db_session):
    await activate(auth_client)
    rows = await seed_transactions(db_session, ("EUR", "USD"))
    response = await auth_client.post(
        "/api/v1/expenses",
        json=report_payload(rows),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422, response.text
    assert await db_session.scalar(select(func.count()).select_from(ExpenseReport)) == 0
    await db_session.refresh(rows[0])
    await db_session.refresh(rows[1])
    assert [row.status for row in rows] == ["available", "available"]


@pytest.mark.asyncio
async def test_same_report_key_recovers_original_report_and_provenance(auth_client, db_session):
    await activate(auth_client)
    rows = await seed_transactions(db_session, ("USD", "USD"))
    key = str(uuid4())
    body = report_payload(rows, currency="USD")
    first = await auth_client.post("/api/v1/expenses", json=body, headers={"Idempotency-Key": key})
    second = await auth_client.post("/api/v1/expenses", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["total"] == "20.00"
    assert {item["currency"] for item in first.json()["items"]} == {"USD"}
    assert {item["original_amount"] for item in first.json()["items"]} == {"10.00"}
    assert await db_session.scalar(select(func.count()).select_from(ExpenseReport)) == 1
    status = await auth_client.get(f"/api/v1/expenses/commands/{key}")
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "committed"
    assert status.json()["target_id"] == first.json()["id"]
    detail = await auth_client.get(f"/api/v1/expenses/{first.json()['id']}")
    assert detail.status_code == 200
    assert detail.json()["total"] == "20.00"


@pytest.mark.asyncio
async def test_removal_replay_is_stable_and_remains_binned(auth_client, db_session):
    await activate(auth_client)
    row = (await seed_transactions(db_session))[0]
    key = str(uuid4())
    kwargs = {
        "params": {"version": getattr(row, "version", 1)},
        "headers": {"Idempotency-Key": key},
    }
    first = await auth_client.delete(f"/api/v1/expenses/transactions/{row.id}", **kwargs)
    second = await auth_client.delete(f"/api/v1/expenses/transactions/{row.id}", **kwargs)
    assert first.status_code == second.status_code == 204, (first.text, second.text)
    assert (await auth_client.get("/api/v1/expenses/transactions")).json() == []
    status = await auth_client.get(f"/api/v1/expenses/commands/{key}")
    assert status.status_code == 200
    assert status.json()["target_id"] == row.id
    assert status.json()["status"] == "committed"


@pytest.mark.asyncio
async def test_oversize_upload_reads_a_bounded_chunk_and_closes(auth_client, monkeypatch):
    from app.services import filesec

    await activate(auth_client)
    calls = []
    closed = []
    original_read, original_close = UploadFile.read, UploadFile.close

    async def read(self, size=-1):
        calls.append(size)
        return await original_read(self, size)

    async def close(self):
        closed.append(True)
        await original_close(self)

    monkeypatch.setattr(UploadFile, "read", read)
    monkeypatch.setattr(UploadFile, "close", close)
    monkeypatch.setattr(filesec, "max_bytes", lambda *args: 32)
    response = await import_statement(auth_client, content=CSV * 10)
    assert response.status_code == 413, response.text
    assert calls and all(0 < size <= 33 for size in calls), calls
    assert closed


@pytest.mark.asyncio
async def test_stale_selection_and_changed_command_payload_are_refused(auth_client, db_session):
    await activate(auth_client)
    rows = await seed_transactions(db_session)
    stale = report_payload(rows)
    stale["transaction_versions"] = {rows[0].id: 99}
    refusal = await auth_client.post(
        "/api/v1/expenses", json=stale, headers={"Idempotency-Key": str(uuid4())}
    )
    assert refusal.status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(ExpenseReport)) == 0
    key = str(uuid4())
    body = report_payload(rows)
    first = await auth_client.post("/api/v1/expenses", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text
    conflict = await auth_client.post(
        "/api/v1/expenses", json={**body, "title": "Changed intention"},
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(ExpenseReport)) == 1


@pytest.mark.asyncio
async def test_exact_replay_does_not_resurrect_a_binned_candidate(auth_client, db_session):
    await activate(auth_client)
    first = await import_statement(auth_client)
    row = first.json()["transactions"][0]
    removed = await auth_client.delete(
        f"/api/v1/expenses/transactions/{row['id']}",
        params={"version": row["version"]}, headers={"Idempotency-Key": str(uuid4())},
    )
    assert removed.status_code == 204
    replay = await import_statement(auth_client)
    assert replay.status_code == 200, replay.text
    assert replay.json()["batch_id"] == first.json()["batch_id"]
    assert replay.json()["imported"] == 0
    assert replay.json()["binned"] == 1
    assert replay.json()["transactions"] == []
    assert (await auth_client.get("/api/v1/expenses/transactions")).json() == []


@pytest.mark.asyncio
async def test_credits_only_is_a_truthful_empty_import(auth_client):
    await activate(auth_client)
    response = await import_statement(auth_client, content=CSV.replace(b"-12.34", b"12.34"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 0
    assert body["skipped_credits"] == 1
    assert body["transactions"] == []
    assert body["warnings"]


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_import_and_key_can_retry(auth_client, db_session, monkeypatch):
    from app.core.errors import AppError
    from app.models.expense import ExpenseImportBatch

    await activate(auth_client)
    key = str(uuid4())

    async def failed_audit(*args, **kwargs):
        raise AppError("Synthetic unavailable audit", status=503)

    with monkeypatch.context() as patch:
        patch.setattr("app.services.expense_commands.audit.record", failed_audit)
        failure = await import_statement(auth_client, key=key)
    assert failure.status_code == 503
    assert await db_session.scalar(select(func.count()).select_from(ExpenseTransaction)) == 0
    assert await db_session.scalar(select(func.count()).select_from(ExpenseImportBatch)) == 0
    assert (await auth_client.get(f"/api/v1/expenses/commands/{key}")).status_code == 404
    retry = await import_statement(auth_client, key=key)
    assert retry.status_code == 200, retry.text
    assert await db_session.scalar(select(func.count()).select_from(ExpenseTransaction)) == 1


@pytest.mark.asyncio
async def test_status_authority_is_employee_tenant_and_permission_scoped(auth_client, client, role_client):
    from tests.test_expense_concur import _h, _member

    await activate(auth_client)
    key = str(uuid4())
    assert (await import_statement(auth_client, key=key)).status_code == 200
    colleague = await _member(auth_client, client, "r010-colleague@example.com")
    denied = await client.get(f"/api/v1/expenses/commands/{key}", headers=_h(colleague))
    assert denied.status_code == 404
    other_org = await role_client("owner")
    assert (await other_org.put("/api/v1/modules/expenses", json={"enabled": True})).status_code == 200
    assert (await other_org.get(f"/api/v1/expenses/commands/{key}")).status_code == 404
    read_only = await role_client("user_free")
    assert (await read_only.get(f"/api/v1/expenses/commands/{key}")).status_code == 403
    unauthenticated = await auth_client.get(
        f"/api/v1/expenses/commands/{key}", headers={"Authorization": ""}
    )
    assert unauthenticated.status_code == 401
