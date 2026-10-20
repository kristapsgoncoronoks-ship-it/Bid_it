"""Manual expense intentions retain exact totals and one atomic saved receipt.

These API checks qualify the existing server foundation used by the manual UI;
they do not prove PostgreSQL concurrency or browser completion.
"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.expense import ExpenseCommand, ExpenseItem, ExpenseReport


def manual_payload(title="Synthetic manual draft", amounts=("0.10", "0.20")):
    return {
        "title": title,
        "currency": "EUR",
        "note": "Synthetic evidence only",
        "items": [
            {
                "spend_date": "2026-10-20",
                "description": f"Synthetic manual item {index}",
                "amount": amount,
                "vat_amount": "0.00",
                "comment": "Synthetic business purpose",
            }
            for index, amount in enumerate(amounts)
        ],
    }


async def enable_expenses(client):
    response = await client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert response.status_code == 200, response.text


async def saved_counts(db):
    return tuple(
        [
            await db.scalar(select(func.count()).select_from(model))
            for model in (ExpenseReport, ExpenseItem, ExpenseCommand)
        ]
        + [
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "expense.report_created")
            )
        ]
    )


async def assert_receipt(client, key, detail):
    response = await client.get(f"/api/v1/expenses/commands/{key}")
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["id"]
    assert receipt["status"] == "committed"
    assert receipt["operation"] == "report_created"
    assert receipt["command_key"] == key
    assert receipt["target_id"] == detail["id"]
    assert receipt["result"] == detail


async def test_manual_key_replays_same_detail_and_lookup_preserves_counts(auth_client, db_session):
    await enable_expenses(auth_client)
    key, body = str(uuid4()), manual_payload()
    first = await auth_client.post("/api/v1/expenses", json=body, headers={"Idempotency-Key": key})
    replay = await auth_client.post("/api/v1/expenses", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == replay.status_code == 201, (first.text, replay.text)
    assert replay.json() == first.json()
    detail = first.json()
    assert detail["status"] == "draft"
    assert detail["total"] == "0.30"
    assert [item["amount"] for item in detail["items"]] == ["0.10", "0.20"]
    for _ in range(3):
        await assert_receipt(auth_client, key, detail)
    assert await saved_counts(db_session) == (1, 2, 1, 1)
    audit = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "expense.report_created")
    )
    assert (audit.target_type, audit.target_id) == ("expense_report", detail["id"])
    assert json.loads(audit.meta)["command_key"] == key
    # The receipt proves the original commit, independently of later editable
    # report state. Recovery must not relabel this frozen snapshot as a new GET.
    changed = await auth_client.patch(
        f"/api/v1/expenses/{detail['id']}", json={"title": "Revised after the commit"}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["title"] != detail["title"]
    await assert_receipt(auth_client, key, detail)
    assert await saved_counts(db_session) == (1, 2, 1, 1)


@pytest.mark.parametrize("explicit_keys", [True, False])
async def test_independent_identical_manual_intentions_create_distinct_drafts(
    auth_client, db_session, explicit_keys
):
    await enable_expenses(auth_client)
    keys = [str(uuid4()), str(uuid4())] if explicit_keys else [None, None]
    reports = []
    for key in keys:
        response = await auth_client.post(
            "/api/v1/expenses",
            json=manual_payload(),
            headers={"Idempotency-Key": key} if key else {},
        )
        assert response.status_code == 201, response.text
        reports.append(response.json()["id"])
    assert len(set(reports)) == 2
    assert await saved_counts(db_session) == (2, 4, 2, 2)
    receipts = list(await db_session.scalars(select(ExpenseCommand)))
    assert len({receipt.command_key for receipt in receipts}) == 2
    if explicit_keys:
        assert {receipt.command_key for receipt in receipts} == set(keys)
    else:
        assert all(receipt.command_key.startswith("legacy-manual:") for receipt in receipts)


async def test_manual_changed_payload_under_original_key_refuses_replacement(
    auth_client, db_session
):
    await enable_expenses(auth_client)
    key, body = str(uuid4()), manual_payload()
    first = await auth_client.post("/api/v1/expenses", json=body, headers={"Idempotency-Key": key})
    conflict = await auth_client.post(
        "/api/v1/expenses",
        json={**body, "title": "Different intention"},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 201, first.text
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "expense_command_payload_conflict"
    await assert_receipt(auth_client, key, first.json())
    assert await saved_counts(db_session) == (1, 2, 1, 1)


@pytest.mark.parametrize("audit_returns_none", [False, True])
async def test_manual_audit_failure_rolls_back_report_items_and_command_then_same_key_retries(
    auth_client, db_session, monkeypatch, audit_returns_none
):
    await enable_expenses(auth_client)
    key = str(uuid4())

    async def unavailable_audit(*args, **kwargs):
        if audit_returns_none:
            return None
        raise AppError("Synthetic unavailable audit", status=503)

    with monkeypatch.context() as scoped:
        scoped.setattr("app.services.expense_commands.audit.record", unavailable_audit)
        failed = await auth_client.post(
            "/api/v1/expenses", json=manual_payload(), headers={"Idempotency-Key": key}
        )
    assert failed.status_code == 503, failed.text
    assert await saved_counts(db_session) == (0, 0, 0, 0)
    assert (await auth_client.get(f"/api/v1/expenses/commands/{key}")).status_code == 404
    retry = await auth_client.post(
        "/api/v1/expenses", json=manual_payload(), headers={"Idempotency-Key": key}
    )
    assert retry.status_code == 201, retry.text
    await assert_receipt(auth_client, key, retry.json())
    assert await saved_counts(db_session) == (1, 2, 1, 1)


@pytest.mark.parametrize(
    ("amounts", "total"),
    [(("0.005", "0.005"), "0.02"), (("0.00", "0.00"), "0.00")],
)
async def test_manual_legacy_server_rounding_and_valid_zero_are_preserved(
    auth_client, amounts, total
):
    await enable_expenses(auth_client)
    response = await auth_client.post(
        "/api/v1/expenses",
        json=manual_payload(amounts=amounts),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    assert response.json()["total"] == total


@pytest.mark.parametrize("above_limit", [False, True])
async def test_manual_total_supported_boundary_is_exact_and_overflow_is_atomic(
    auth_client, db_session, above_limit
):
    await enable_expenses(auth_client)
    amounts = (
        "500000000000.00" if above_limit else "499999999999.99",
        "500000000000.00",
    )
    key = str(uuid4())
    response = await auth_client.post(
        "/api/v1/expenses",
        json=manual_payload(amounts=amounts),
        headers={"Idempotency-Key": key},
    )
    if above_limit:
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "expense_total_out_of_range"
        assert await saved_counts(db_session) == (0, 0, 0, 0)
        assert (await auth_client.get(f"/api/v1/expenses/commands/{key}")).status_code == 404
    else:
        assert response.status_code == 201, response.text
        assert response.json()["total"] == "999999999999.99"
        assert await saved_counts(db_session) == (1, 2, 1, 1)
