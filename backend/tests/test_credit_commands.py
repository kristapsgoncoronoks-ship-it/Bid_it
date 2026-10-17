"""R016: a repeated partial credit command must not create two legal notes."""

import pytest
from sqlalchemy import func, select

from app.models.issued_invoice import IssuedInvoice
from tests.test_r011_invoice_commands import create

KEY = {"Idempotency-Key": "synthetic-credit-command"}
BODY = {
    "lines": [
        {
            "description": "Synthetic partial credit",
            "quantity": "1",
            "unit_price": "10",
            "vat_rate": "21",
        }
    ]
}


@pytest.mark.asyncio
async def test_partial_credit_replay_has_one_legal_note(auth_client, db_session):
    inv = await create(auth_client)
    first = await auth_client.post(
        f"/api/v1/issued/{inv['id']}/credit-note", json=BODY, headers=KEY
    )
    second = await auth_client.post(
        f"/api/v1/issued/{inv['id']}/credit-note", json=BODY, headers=KEY
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(IssuedInvoice)
            .where(IssuedInvoice.doc_type == "credit_note")
        )
        == 1
    )
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_full_credit_replay_converges_after_balance_changes(auth_client):
    inv = await create(auth_client)
    first = await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json={}, headers=KEY)
    second = await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json={}, headers=KEY)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_credit_command_binding_refuses_stale_inputs_and_keeps_one_audit(
    auth_client, db_session
):
    from app.models.audit import AuditEvent

    inv = await create(auth_client)
    path = f"/api/v1/issued/{inv['id']}/credit-note"
    assert (await auth_client.post(path, json={})).status_code == 428
    stale = await auth_client.post(
        path, json={**BODY, "expected_credited_total": "1.00"}, headers=KEY
    )
    assert stale.status_code == 409 and stale.json()["code"] == "credit_balance_changed"
    first = await auth_client.post(path, json=BODY, headers=KEY)
    assert first.status_code == 201
    assert (
        await auth_client.post(path, json={"reason": "Changed"}, headers=KEY)
    ).status_code == 409
    lookup = await auth_client.get(
        f"/api/v1/issued/{inv['id']}/credit-commands/synthetic-credit-command"
    )
    assert lookup.status_code == 200 and lookup.json()["id"] == first.json()["id"]
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "issued.credit_note")
    )
    assert event.target_id == first.json()["id"]
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "issued.credit_note")
        )
        == 1
    )
