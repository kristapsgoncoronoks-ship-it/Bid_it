"""Version-pinned child outcomes and text-mail intent on the native durable queue."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.models.automation import AutomationRule, AutomationRun
from app.models.customer import Customer
from app.models.email_message import EmailMessage
from app.models.job import Job
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.services import audit, jobs, mailer

KIND = "automation.deliver"
MAIL_ACTIONS = {"notify_owner_email", "notify_customer_email"}


async def _recipient(db, org_id, kind, context):
    if kind == "notify_owner_email":
        user = await db.scalar(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == org_id,
                Membership.status == "active",
                Membership.role == UserRole.owner,
                User.is_active.is_(True),
            )
            .order_by(User.id)
            .limit(1)
        )
        return (user.email, user.id) if user else (None, None)
    customer = await db.scalar(
        select(Customer).where(
            Customer.org_id == org_id,
            Customer.id == context.get("customer_id"),
            Customer.is_active.is_(True),
        )
    )
    return (customer.email, None) if customer else (None, None)


async def _load_run(db, org_id, run_id):
    run = await db.scalar(
        select(AutomationRun)
        .where(AutomationRun.org_id == org_id, AutomationRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise NotFoundError("Automation run not found.")
    try:
        data = json.loads(run.detail_json or "null")
    except ValueError as exc:
        raise ConflictError("Stored run evidence is invalid. Restore it before recovery.") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 2:
        raise ConflictError(
            "Legacy runs require evidence review; they cannot be replayed automatically."
        )
    return run, data


def _state(children):
    states = {child["state"] for child in children}
    for state, result in (
        ("unknown", "unknown"),
        ("sending", "pending"),
        ("queued", "pending"),
        ("blocked", "blocked"),
        ("failed", "failed"),
    ):
        if state in states:
            return result
    return "ok"


async def _save(db, run, data):
    # Refresh message outcomes under the parent lock to avoid losing a sibling
    # worker's completion when multiple child messages finish concurrently.
    for child in data["actions"]:
        if child.get("message_id"):
            message = await db.scalar(
                select(EmailMessage)
                .where(EmailMessage.org_id == run.org_id, EmailMessage.id == child["message_id"])
                .execution_options(populate_existing=True)
            )
            if message is None:
                child.update(
                    state="failed", reason="Delivery intent is missing; restore it before retrying."
                )
            else:
                child["state"] = "done" if message.status == "sent" else message.status
                child["reason"] = message.error
                child["message_status"] = message.status
                child["accepted_at"] = (
                    message.sent_at.isoformat() if message.sent_at is not None else None
                )
    run.status = _state(data["actions"])
    run.detail_json = json.dumps(data)
    await db.flush()


async def _prepare_mail(db, run, data, child):
    from app.services.automation import render_text

    definition = child["definition"]
    key = f"automation:{run.id}:{child['index']}"
    message = await db.scalar(
        select(EmailMessage).where(
            EmailMessage.org_id == run.org_id, EmailMessage.command_key == key
        )
    )
    if message is None and child.get("message_id"):
        raise ConflictError(
            "Delivery evidence is missing. Restore the intent; do not recreate or resend it."
        )
    if message is None:
        recipient, owner_id = await _recipient(db, run.org_id, child["kind"], data["context"])
        message = EmailMessage(
            org_id=run.org_id,
            kind="automation",
            command_key=key,
            to_email=recipient or "",
            from_email=settings.smtp_from,
            subject=render_text(definition["subject"], data["context"]),
            body=render_text(definition["body"], data["context"]),
            status="blocked",
        )
        db.add(message)
        await db.flush()
        child["recipient_owner_id"] = owner_id
        child["message_id"] = message.id
    if message.status in {"sent", "unknown", "sending"}:
        return
    generation = int(child.get("queue_generation", 0))
    existing = await db.scalar(
        select(Job).where(
            Job.org_id == run.org_id,
            Job.kind == KIND,
            Job.idempotency_key == f"{message.id}:{generation}",
        )
    )
    if existing is not None:
        if existing.status in {"queued", "running"}:
            return
        generation += 1
    child["queue_generation"] = generation
    if not message.to_email:
        recipient, owner_id = await _recipient(db, run.org_id, child["kind"], data["context"])
        message.to_email = recipient or ""
        child["recipient_owner_id"] = owner_id
    if not message.to_email:
        message.status = "blocked"
        message.error = (
            "No eligible recipient email. Correct the owner/customer record, then retry this run."
        )
    elif not settings.smtp_enabled:
        message.status = "blocked"
        message.error = "SMTP is not configured. Configure the relay, then retry this run."
    else:
        message.status = "queued"
        message.error = None
        await jobs.enqueue(
            db,
            KIND,
            {"run_id": run.id, "message_id": message.id},
            org_id=run.org_id,
            idempotency_key=f"{message.id}:{generation}",
            commit=False,
        )
    await db.flush()


async def _child(db, run, data, child):
    from app.services import automation

    if child["state"] == "done":
        return
    if child["kind"] in MAIL_ACTIONS:
        await _prepare_mail(db, run, data, child)
        return
    rule = SimpleNamespace(id=run.rule_id, **data["definition"])
    result = await automation._run_action(
        db, run.org_id, child["definition"], data["context"], rule=rule, ref_id=run.ref_id
    )
    child.update(state="done" if result["ok"] else "blocked", result=result)


async def begin(db, org_id, rule, ref_id, context, actions):
    run = AutomationRun(
        org_id=org_id,
        rule_id=rule.id,
        version=rule.published_version,
        ref_id=ref_id,
        status="pending",
    )
    db.add(run)
    await db.flush()
    data = {
        "schema_version": 2,
        "context": context,
        "definition": {"name": rule.name, "trigger": rule.trigger},
        "actions": [
            {"index": index, "kind": action["kind"], "definition": action, "state": "planned"}
            for index, action in enumerate(actions)
        ],
    }
    for child in data["actions"]:
        await _child(db, run, data, child)
    await _save(db, run, data)
    return run


async def retry(db, org_id, run_id):
    run, data = await _load_run(db, org_id, run_id)
    if run.status == "ok":
        return run
    before = run.detail_json
    if run.status == "unknown" or any(
        c["state"] in {"unknown", "sending"} for c in data["actions"]
    ):
        raise ConflictError(
            "SMTP acceptance is uncertain. Check relay evidence; this run cannot be resent automatically."
        )
    rule = await db.scalar(
        select(AutomationRule).where(
            AutomationRule.org_id == org_id, AutomationRule.id == run.rule_id
        )
    )
    if rule is None or rule.status != "published":
        raise ConflictError("Enable the reviewed rule before retrying its frozen run.")
    for child in data["actions"]:
        await _child(db, run, data, child)
    await _save(db, run, data)
    if run.detail_json == before:
        return run
    event = await audit.record(
        db,
        "automation.run_retry",
        org_id=org_id,
        target_type="automation_run",
        target_id=run.id,
        meta={"version": run.version, "status": run.status},
    )
    if event is None:
        raise RuntimeError("Automation retry audit could not be recorded.")
    return run


async def deliver(db, org_id, run_id, message_id):
    run, data = await _load_run(db, org_id, run_id)
    child = next((c for c in data["actions"] if c.get("message_id") == message_id), None)
    if child is None:
        raise NotFoundError("Automation delivery intent not found.")
    message = await db.scalar(
        select(EmailMessage)
        .where(EmailMessage.org_id == org_id, EmailMessage.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if message is None or message.kind != "automation":
        raise NotFoundError("Automation delivery intent not found.")
    if message.status in {"sent", "unknown", "blocked", "failed"}:
        await _save(db, run, data)
        await db.commit()
        return {"status": message.status}
    if message.status == "sending":
        message.status = "unknown"
        message.error = "Previous SMTP attempt has no confirmed outcome. Check relay evidence before any resend."
        await db.flush()
        await _save(db, run, data)
        await db.commit()
        return {"status": "unknown"}
    if message.status != "queued":
        raise jobs.PermanentJobError("Automation delivery is not queued.")
    rule = await db.scalar(
        select(AutomationRule).where(
            AutomationRule.org_id == org_id, AutomationRule.id == run.rule_id
        )
    )
    org = await db.get(Organization, org_id)
    allowed = (
        org is not None
        and org.status == "active"
        and rule is not None
        and rule.status == "published"
    )
    if child.get("recipient_owner_id"):
        recipient, owner_id = await _recipient(db, org_id, child["kind"], data["context"])
        allowed = (
            allowed and owner_id == child["recipient_owner_id"] and recipient == message.to_email
        )
    if not allowed or not settings.smtp_enabled:
        message.status = "blocked"
        message.error = (
            "Rule/workspace/recipient or SMTP is unavailable. Review and retry the frozen run."
        )
        await db.flush()
        await _save(db, run, data)
        await db.commit()
        return {"status": "blocked"}
    sender, recipient, subject, body = (
        message.from_email,
        message.to_email,
        message.subject,
        message.body,
    )
    message.status = "sending"
    await db.flush()
    await _save(db, run, data)
    await db.commit()
    # Durable sending marker precedes external I/O. Any exception/reclaim after
    # this point is unknown, not permission for another send.
    try:
        await run_in_threadpool(mailer._smtp_send, sender, recipient, subject, body, None)
        outcome = "sent"
    except Exception:
        outcome = "unknown"
    run, data = await _load_run(db, org_id, run_id)
    message = await db.scalar(
        select(EmailMessage)
        .where(EmailMessage.org_id == org_id, EmailMessage.id == message_id)
        .execution_options(populate_existing=True)
    )
    message.status = outcome
    message.error = (
        None
        if outcome == "sent"
        else "SMTP acceptance is uncertain. Check relay evidence; automatic resend is blocked."
    )
    if outcome == "sent":
        message.sent_at = datetime.now(UTC)
    await db.flush()
    await _save(db, run, data)
    event = await audit.record(
        db,
        "automation.delivery_outcome",
        org_id=org_id,
        target_type="automation_run",
        target_id=run.id,
        meta={"message_id": message_id, "status": outcome},
    )
    if event is None:
        raise RuntimeError("Automation delivery audit could not be recorded.")
    await db.commit()
    return {"status": outcome}
