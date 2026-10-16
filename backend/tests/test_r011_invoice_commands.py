"""R011: retries preserve a legal document and truthful durable delivery state."""

import json

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.audit import AuditEvent
from app.models.email_message import EmailMessage
from app.models.issued_invoice import IssuedInvoice
from app.models.job import Job
from app.services import invoice_delivery, jobs, mailer
from tests.test_issuing import INVOICE, _activate

KEY = {"Idempotency-Key": "r011-test-command"}


async def create(auth_client, *, draft=False, key=None):
    await _activate(auth_client)
    response = await auth_client.post(
        "/api/v1/issued",
        json={**INVOICE, "draft": draft, "buyer_email": "buyer@example.com"},
        headers=key or {},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("draft", [True, False])
async def test_create_replay_and_lookup_commit_one_document(auth_client, db_session, draft):
    first = await create(auth_client, draft=draft, key=KEY)
    second = await create(auth_client, draft=draft, key=KEY)
    assert second["id"] == first["id"]
    assert second["number"] == first["number"]
    assert await db_session.scalar(select(func.count()).select_from(IssuedInvoice)) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "issued.create")
        )
        == 1
    )
    lookup = await auth_client.get("/api/v1/issued/commands/r011-test-command")
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["id"] == first["id"]


async def test_changed_payload_cannot_reuse_a_command(auth_client, db_session):
    first = await create(auth_client, key=KEY)
    response = await auth_client.post(
        "/api/v1/issued",
        json={**INVOICE, "buyer_name": "Different Buyer"},
        headers=KEY,
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "command_payload_conflict"
    assert await db_session.scalar(select(func.count()).select_from(IssuedInvoice)) == 1
    assert (await auth_client.get(f"/api/v1/issued/{first['id']}")).json()["buyer_name"] == INVOICE[
        "buyer_name"
    ]


async def test_issue_replay_keeps_one_number_and_one_audit(auth_client, db_session):
    invoice = await create(auth_client, draft=True)
    url = f"/api/v1/issued/{invoice['id']}/issue"
    first = await auth_client.post(url, json={}, headers=KEY)
    assert first.status_code == 200, first.text
    second = await auth_client.post(url, json={}, headers=KEY)
    assert second.status_code == 200, second.text
    assert second.json()["number"] == first.json()["number"]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "issued.issue")
        )
        == 1
    )
    lookup = await auth_client.get("/api/v1/issued/commands/r011-test-command?action=issue")
    assert lookup.status_code == 200
    assert lookup.json()["id"] == invoice["id"]


@pytest.mark.parametrize("header", ["X-Expected-Org-ID", "X-Expected-User-ID"])
async def test_stale_account_or_company_cannot_issue_in_a_new_scope(
    auth_client, db_session, header
):
    await _activate(auth_client)
    response = await auth_client.post(
        "/api/v1/issued", json=INVOICE, headers={**KEY, header: "wrong-scope"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "identity_changed"
    assert await db_session.scalar(select(func.count()).select_from(IssuedInvoice)) == 0


async def test_missing_smtp_is_blocked_not_queued_or_sent(auth_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", None)
    invoice = await create(auth_client)
    response = await auth_client.post(f"/api/v1/issued/{invoice['id']}/send", json={}, headers=KEY)
    assert response.status_code == 200, response.text
    assert response.json()["message"]["status"] == "blocked"
    assert response.json()["delivered"] is False
    assert response.json()["already_sent"] is False
    assert "Configure" in response.json()["message"]["error"]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == invoice_delivery.KIND)
        )
        == 0
    )
    assert (await auth_client.get(f"/api/v1/issued/{invoice['id']}")).json()["sent_at"] is None


async def queue(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "synthetic.test")
    invoice = await create(auth_client)
    response = await auth_client.post(f"/api/v1/issued/{invoice['id']}/send", json={}, headers=KEY)
    assert response.status_code == 200, response.text
    assert response.json()["message"]["status"] == "queued"
    assert response.json()["delivered"] is False
    return invoice, response.json()["message"]


async def test_delivery_intent_and_job_commit_before_smtp(auth_client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
    invoice, message = await queue(auth_client, monkeypatch)
    assert calls == []
    job = await db_session.scalar(select(Job).where(Job.kind == invoice_delivery.KIND))
    assert job is not None and json.loads(job.payload_json)["message_id"] == message["id"]
    again = await auth_client.post(f"/api/v1/issued/{invoice['id']}/send", json={}, headers=KEY)
    assert again.status_code == 200
    assert again.json()["message"]["id"] == message["id"]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == invoice_delivery.KIND)
        )
        == 1
    )
    await jobs.run_once(db_session, "r011-worker")
    assert len(calls) == 1
    await db_session.refresh(job)
    assert job.status == "succeeded"
    lookup = await auth_client.get(
        f"/api/v1/issued/{invoice['id']}/delivery-commands/r011-test-command"
    )
    assert lookup.status_code == 200
    assert lookup.json()["message"]["status"] == "sent"
    assert lookup.json()["message"]["sent_at"] is not None
    assert (await auth_client.get(f"/api/v1/issued/{invoice['id']}")).json()["sent_at"] is not None
    row = await db_session.get(EmailMessage, message["id"])
    await invoice_delivery.deliver(db_session, row.org_id, row.id)
    assert len(calls) == 1


async def test_uncertain_smtp_outcome_is_not_retried_blindly(auth_client, db_session, monkeypatch):
    calls = []

    def lost_response(*args):
        calls.append(args)
        raise OSError("synthetic lost SMTP response")

    monkeypatch.setattr(mailer, "_smtp_send", lost_response)
    invoice, message = await queue(auth_client, monkeypatch)
    await jobs.run_once(db_session, "r011-worker")
    row = await db_session.get(EmailMessage, message["id"])
    assert row.status == "unknown"
    assert row.sent_at is None
    await invoice_delivery.deliver(db_session, row.org_id, row.id)
    assert len(calls) == 1
    response = await auth_client.post(f"/api/v1/issued/{invoice['id']}/send", json={}, headers=KEY)
    assert response.json()["message"]["status"] == "unknown"
    assert response.json()["already_sent"] is False


async def test_reclaimed_sending_intent_becomes_unknown_without_smtp(
    auth_client, db_session, monkeypatch
):
    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
    _, message = await queue(auth_client, monkeypatch)
    row = await db_session.get(EmailMessage, message["id"])
    row.status = "sending"
    await db_session.commit()
    result = await invoice_delivery.deliver(db_session, row.org_id, row.id)
    assert result["status"] == "unknown"
    assert calls == []


async def test_restoring_smtp_can_queue_a_previously_blocked_command(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "smtp_host", None)
    invoice = await create(auth_client)
    url = f"/api/v1/issued/{invoice['id']}/send"
    before = await auth_client.post(url, json={}, headers=KEY)
    assert before.json()["message"]["status"] == "blocked"
    monkeypatch.setattr(settings, "smtp_host", "synthetic.test")
    after = await auth_client.post(url, json={}, headers=KEY)
    assert after.json()["message"]["status"] == "queued"
    assert after.json()["message"]["id"] == before.json()["message"]["id"]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "invoice")
        )
        == 1
    )


async def test_different_primary_keys_do_not_queue_a_second_delivery(
    auth_client, db_session, monkeypatch
):
    invoice, message = await queue(auth_client, monkeypatch)
    response = await auth_client.post(
        f"/api/v1/issued/{invoice['id']}/send",
        json={},
        headers={"Idempotency-Key": "r011-another-primary"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["message"]["id"] == message["id"]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == invoice_delivery.KIND)
        )
        == 1
    )


async def test_missing_attachment_reports_failure_and_does_not_send(
    auth_client, db_session, monkeypatch
):
    from app.core import storage

    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
    _, message = await queue(auth_client, monkeypatch)
    row = await db_session.get(EmailMessage, message["id"])
    storage.get_storage().delete(
        storage.content_key("invoice-delivery", row.org_id, row.attachment_sha256)
    )
    job = await jobs.run_once(db_session, "r011-worker")
    await db_session.refresh(row)
    assert row.status == "failed"
    assert job.status == "dead"
    assert calls == []


async def test_queue_failure_rolls_back_intent_and_audit(auth_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "synthetic.test")
    invoice = await create(auth_client)

    async def fail_enqueue(*args, **kwargs):
        raise RuntimeError("synthetic queue failure")

    monkeypatch.setattr(jobs, "enqueue", fail_enqueue)
    with pytest.raises(RuntimeError, match="synthetic queue failure"):
        await auth_client.post(f"/api/v1/issued/{invoice['id']}/send", json={}, headers=KEY)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "invoice")
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "issued.delivery_queued")
        )
        == 0
    )


async def test_other_tenant_cannot_lookup_committed_commands(auth_client, client):
    invoice = await create(auth_client, key=KEY)
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Synthetic Tenant",
            "name": "Other Owner",
            "email": "other-r011@example.com",
            "password": "synthetic-test-password",
        },
    )
    assert response.status_code == 201
    headers = {"Authorization": "Bearer " + response.json()["token"]["access_token"]}
    lookup = await client.get("/api/v1/issued/commands/r011-test-command", headers=headers)
    assert lookup.status_code == 404

    lookup = await client.get(
        f"/api/v1/issued/{invoice['id']}/delivery-commands/r011-test-command", headers=headers
    )
    assert lookup.status_code == 404


async def test_keyed_create_cannot_commit_without_its_audit(auth_client, db_session, monkeypatch):
    from app.services import audit

    await _activate(auth_client)

    async def unavailable(*args, **kwargs):
        return None

    monkeypatch.setattr(audit, "record", unavailable)
    with pytest.raises(RuntimeError, match="audit could not be recorded"):
        await auth_client.post("/api/v1/issued", json=INVOICE, headers=KEY)
    assert await db_session.scalar(select(func.count()).select_from(IssuedInvoice)) == 0
