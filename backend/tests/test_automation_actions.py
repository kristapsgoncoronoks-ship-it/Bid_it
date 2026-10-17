"""FP-P02: committed child intent and truthful SMTP outcomes, synthetic transport."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.audit import AuditEvent
from app.models.automation import AutomationRule, AutomationRun
from app.models.crm import CustomerNote
from app.models.email_message import EmailMessage
from app.models.job import Job
from app.services import automation, automation_actions, mailer
from tests.automation_client import reviewed_request
from tests.test_automation import STALE_RULE


async def prepare(client, db, monkeypatch, *, smtp=False):
    monkeypatch.setattr(settings, "smtp_host", "synthetic-relay.test" if smtp else None)
    row = (await client.post("/api/v1/automation/rules", json=STALE_RULE)).json()
    response = await reviewed_request(
        client, "post", f"/api/v1/automation/rules/{row['id']}/publish"
    )
    assert response.status_code == 200
    rule = await db.get(AutomationRule, row["id"])
    live = await automation._execution_rule(db, rule.org_id, rule)
    return live


@pytest.mark.asyncio
async def test_no_smtp_is_blocked_not_success_and_replay_reuses_intent(
    auth_client, db_session, monkeypatch
):
    live = await prepare(auth_client, db_session, monkeypatch)
    run = await automation_actions.begin(
        db_session,
        live.org_id,
        live,
        "synthetic-record",
        {"days_quiet": 10, "offer_number": "SYNTH-1"},
        [{"kind": "notify_owner_email", "subject": "Synthetic", "body": "Synthetic body"}],
    )
    await db_session.commit()
    assert run.status == "blocked"
    message = await db_session.scalar(select(EmailMessage).where(EmailMessage.kind == "automation"))
    assert message.status == "blocked" and message.sent_at is None
    assert await db_session.scalar(select(func.count()).select_from(Job)) == 0
    for _ in range(2):
        await automation_actions.retry(db_session, live.org_id, run.id)
        await db_session.commit()
    assert (
        await db_session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "automation")
        )
        == 1
    )
    assert not await automation._may_fire(
        db_session, live.org_id, live, run.ref_id, automation.datetime.now(automation.UTC)
    )


@pytest.mark.asyncio
async def test_intent_commits_before_send_and_acceptance_updates_parent(
    auth_client, db_session, monkeypatch, _db
):
    live = await prepare(auth_client, db_session, monkeypatch, smtp=True)
    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
    run = await automation_actions.begin(
        db_session,
        live.org_id,
        live,
        "synthetic-record",
        {},
        [{"kind": "notify_owner_email", "subject": "Frozen", "body": "Frozen body"}],
    )
    assert not calls and run.status == "pending"
    await db_session.commit()
    message = await db_session.scalar(select(EmailMessage).where(EmailMessage.kind == "automation"))
    message_id, run_id, org_id = message.id, run.id, live.org_id

    async def send(*args):
        async with _db() as independent:
            stored = await independent.get(EmailMessage, message_id)
            assert stored.status == "sending"
        calls.append(args)

    # At the async thread boundary we inspect a separate session; no SMTP contact.
    monkeypatch.setattr(automation_actions, "run_in_threadpool", lambda fn, *args: send(*args))
    result = await automation_actions.deliver(db_session, org_id, run_id, message_id)
    assert result["status"] == "sent" and len(calls) == 1
    await db_session.refresh(run)
    await db_session.refresh(message)
    assert run.status == "ok" and message.sent_at is not None
    await automation_actions.deliver(db_session, org_id, run_id, message_id)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_uncertain_send_and_reclaimed_sending_never_resend(
    auth_client, db_session, monkeypatch
):
    live = await prepare(auth_client, db_session, monkeypatch, smtp=True)
    calls = []

    def disconnected(*args):
        calls.append(True)
        raise OSError("synthetic uncertain response")

    monkeypatch.setattr(mailer, "_smtp_send", disconnected)
    run = await automation_actions.begin(
        db_session,
        live.org_id,
        live,
        "synthetic-record",
        {},
        [{"kind": "notify_owner_email", "subject": "Frozen", "body": "Frozen"}],
    )
    await db_session.commit()
    message = await db_session.scalar(select(EmailMessage).where(EmailMessage.kind == "automation"))
    assert (await automation_actions.deliver(db_session, live.org_id, run.id, message.id))[
        "status"
    ] == "unknown"
    assert len(calls) == 1
    from app.core.errors import ConflictError

    with pytest.raises(ConflictError):
        await automation_actions.retry(db_session, live.org_id, run.id)
    await db_session.rollback()
    # Simulate a different committed sending marker left by a crashed worker.
    run = await automation_actions.begin(
        db_session,
        live.org_id,
        live,
        "second-record",
        {},
        [{"kind": "notify_owner_email", "subject": "Frozen", "body": "Frozen"}],
    )
    await db_session.commit()
    message = await db_session.scalar(
        select(EmailMessage).where(EmailMessage.command_key == f"automation:{run.id}:0")
    )
    message.status = "sending"
    await db_session.commit()
    assert (await automation_actions.deliver(db_session, live.org_id, run.id, message.id))[
        "status"
    ] == "unknown"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_partial_retry_never_duplicates_completed_note(auth_client, db_session, monkeypatch):
    live = await prepare(auth_client, db_session, monkeypatch)
    assert (
        await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    ).status_code == 200
    response = await auth_client.post(
        "/api/v1/customers", json={"name": "Synthetic retry customer"}
    )
    assert response.status_code == 201
    cid = response.json()["id"]
    run = await automation_actions.begin(
        db_session,
        live.org_id,
        live,
        cid,
        {"customer_id": cid},
        [
            {"kind": "create_customer_note", "body": "One durable note"},
            {"kind": "notify_customer_email", "subject": "Frozen", "body": "Frozen"},
        ],
    )
    await db_session.commit()
    assert run.status == "blocked"
    assert await db_session.scalar(select(func.count()).select_from(CustomerNote)) == 1
    from app.models.customer import Customer

    customer = await db_session.get(Customer, cid)
    customer.email = "synthetic-retry@example.com"
    await db_session.commit()
    monkeypatch.setattr(settings, "smtp_host", "synthetic-relay.test")
    for _ in range(2):
        await automation_actions.retry(db_session, live.org_id, run.id)
        await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(CustomerNote)) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "automation")
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == automation_actions.KIND)
        )
        == 1
    )
    await db_session.refresh(run)
    assert run.status == "pending"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "automation.run_retry")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_transaction_failure_rolls_back_note_intent_and_job(
    auth_client, db_session, monkeypatch, _db
):
    live = await prepare(auth_client, db_session, monkeypatch, smtp=True)

    async def broken(*args, **kwargs):
        raise RuntimeError("synthetic enqueue failure")

    monkeypatch.setattr(automation_actions.jobs, "enqueue", broken)
    with pytest.raises(RuntimeError, match="synthetic"):
        await automation_actions.begin(
            db_session,
            live.org_id,
            live,
            "synthetic-record",
            {},
            [{"kind": "notify_owner_email", "subject": "Frozen", "body": "Frozen"}],
        )
    await db_session.rollback()
    async with _db() as verify:
        assert await verify.scalar(select(func.count()).select_from(AutomationRun)) == 0
        assert (
            await verify.scalar(
                select(func.count())
                .select_from(EmailMessage)
                .where(EmailMessage.kind == "automation")
            )
            == 0
        )
        assert (
            await verify.scalar(
                select(func.count()).select_from(Job).where(Job.kind == automation_actions.KIND)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_legacy_partial_outcome_is_not_automatically_replayed(
    auth_client, db_session, monkeypatch
):
    live = await prepare(auth_client, db_session, monkeypatch)
    prior = AutomationRun(
        org_id=live.org_id,
        rule_id=live.id,
        version=1,
        ref_id="legacy-partial",
        status="failed",
        detail_json=json.dumps(
            [
                {"kind": "create_customer_note", "ok": True},
                {"kind": "notify_owner_email", "ok": False},
            ]
        ),
    )
    db_session.add(prior)
    await db_session.commit()
    reason = await automation._suppression_reason(
        db_session, live.org_id, live, prior.ref_id, datetime.now(UTC)
    )
    assert reason == "legacy_partial_requires_review"
    from app.core.errors import ConflictError

    with pytest.raises(ConflictError, match="Legacy"):
        await automation_actions.retry(db_session, live.org_id, prior.id)
