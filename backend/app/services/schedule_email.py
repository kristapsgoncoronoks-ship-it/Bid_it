"""Committed work-assignment email intent, with conservative SMTP outcomes.

Queued is durable work; sent means SMTP acceptance, not recipient delivery.
An uncertain send is never retried automatically. This module does not commit
when queueing intent; the scheduling route owns its mutation/audit transaction.
"""

from __future__ import annotations

import hashlib
import json
import smtplib
import uuid
from datetime import UTC, datetime

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.costing import Project
from app.models.customer import Customer
from app.models.email_message import EmailMessage
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.project_assignment import ProjectAssignment
from app.models.user import User
from app.services import jobs, mailer

KIND = "schedule.email"
_TERMINAL = {"sent", "unknown", "failed", "blocked"}
_DEFINITE_REJECTIONS = (
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPDataError,
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    smtplib.SMTPNotSupportedError,
)


def prefix(assignment_id: str, revision: int | None = None) -> str:
    return f"assignment:{assignment_id}:" + (f"{revision}:" if revision is not None else "")


def _fingerprint(message: EmailMessage, context: dict) -> str:
    value = {
        **context,
        "to": message.to_email,
        "from": message.from_email,
        "subject": message.subject,
        "body": message.body,
        "kind": message.kind,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def queue(
    db: AsyncSession,
    row: ProjectAssignment,
    *,
    category: str,
    kind: str,
    subject: str,
    body: str,
    to_email: str | None = None,
    recipient_user_id: str | None = None,
) -> EmailMessage:
    recipient = to_email or row.assignee_email
    revision = row.revision or 1
    if category != "client_notice" and recipient_user_id is None:
        recipient_user_id = row.assignee_user_id
    context = {
        "assignment_id": row.id,
        "revision": revision,
        "category": category,
        "recipient_user_id": recipient_user_id,
    }
    recipient_key = hashlib.sha256(recipient.casefold().encode()).hexdigest()[:16]
    key = f"{prefix(row.id, revision)}{category}:{recipient_key}"
    message = await db.scalar(
        select(EmailMessage).where(
            EmailMessage.org_id == row.org_id,
            EmailMessage.command_key == key,
        )
    )
    if message is not None:
        return message
    message = EmailMessage(
        org_id=row.org_id,
        kind=kind,
        to_email=recipient,
        subject=subject,
        body=body,
        command_key=key,
        from_email=settings.smtp_from,
        status="queued" if settings.smtp_enabled else "blocked",
    )
    message.command_hash = _fingerprint(message, context)
    if not settings.smtp_enabled:
        message.error = "Email relay is not configured. Configure SMTP before retrying this notice."
    db.add(message)
    await db.flush()
    if message.status == "queued":
        await jobs.enqueue(
            db,
            KIND,
            {"message_id": message.id, **context},
            org_id=row.org_id,
            idempotency_key=message.id,
            commit=False,
        )
    return message


async def _active_user(db: AsyncSession, org_id: str, user_id: str) -> User | None:
    member = await db.scalar(
        select(Membership).where(
            Membership.org_id == org_id,
            Membership.user_id == user_id,
            Membership.status == "active",
        )
    )
    user = (
        await db.scalar(
            select(User).where(User.id == user_id).execution_options(populate_existing=True)
        )
        if member is not None
        else None
    )
    return user if user is not None and user.is_active else None


async def _eligible(
    db: AsyncSession,
    row: ProjectAssignment,
    message: EmailMessage,
    context: dict,
) -> bool:
    org = await db.scalar(
        select(Organization)
        .where(Organization.id == row.org_id)
        .execution_options(populate_existing=True)
    )
    if org is None or org.status != "active":
        return False
    category = context["category"]
    recipient_user_id = context["recipient_user_id"]
    if category == "reassigned":
        if not row.notify_assignee or recipient_user_id == row.assignee_user_id:
            return False
    elif row.revision != context["revision"]:
        return False
    if category == "client_notice":
        if row.status in {"done", "cancelled"} or row.client_notice_enabled is False:
            return False
        if (
            row.client_notice_enabled is None
            and row.client_notice_hours_before is None
            and org.client_notice_hours is None
        ):
            return False
        if await _active_user(db, row.org_id, row.assignee_user_id) is None:
            return False
        project = await db.scalar(
            select(Project)
            .where(
                Project.org_id == row.org_id,
                Project.id == row.project_id,
            )
            .execution_options(populate_existing=True)
        )
        customer = (
            await db.scalar(
                select(Customer)
                .where(
                    Customer.org_id == row.org_id,
                    Customer.id == project.customer_id,
                )
                .execution_options(populate_existing=True)
            )
            if project is not None and project.customer_id is not None
            else None
        )
        return customer is not None and customer.email == message.to_email
    if recipient_user_id is None:
        return False
    if category == "reminder":
        if (
            not row.reminders_enabled
            or row.status in {"done", "cancelled"}
            or row.reminder_sent_at is not None
        ):
            return False
    elif not row.notify_assignee:
        return False
    if category == "cancelled" and row.status != "cancelled":
        return False
    if category in {"assigned", "changed"} and row.status in {"done", "cancelled"}:
        return False
    user = await _active_user(db, row.org_id, recipient_user_id)
    return user is not None and user.email == message.to_email


async def deliver(db: AsyncSession, org_id: str, payload: dict) -> dict:
    context = {
        key: payload.get(key)
        for key in (
            "assignment_id",
            "revision",
            "category",
            "recipient_user_id",
        )
    }
    # Same lock order as schedule mutations: assignment, then its email intent.
    row = await db.scalar(
        select(ProjectAssignment)
        .where(
            ProjectAssignment.org_id == org_id,
            ProjectAssignment.id == context["assignment_id"],
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    message = await db.scalar(
        select(EmailMessage)
        .where(
            EmailMessage.org_id == org_id,
            EmailMessage.id == payload.get("message_id"),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if message is None:
        raise jobs.PermanentJobError("Assignment email intent is unavailable.")
    if message.command_hash != _fingerprint(message, context):
        raise jobs.PermanentJobError("Assignment email intent does not match its queued identity.")
    if message.status in _TERMINAL:
        return {"status": message.status}
    if message.status == "sending":
        message.status = "unknown"
        message.error = (
            "A prior send has no confirmed outcome. Check the mail relay before resending."
        )
        await db.commit()
        return {"status": "unknown"}
    if message.status != "queued":
        raise jobs.PermanentJobError("Assignment email is not queued.")
    if row is None or not await _eligible(db, row, message, context):
        message.status = "blocked"
        message.error = "The assignment or recipient changed. This notice was not sent."
        await db.commit()
        return {"status": "blocked", "reason": "stale"}
    if not settings.smtp_enabled:
        message.status = "blocked"
        message.error = "Email relay is not configured. Configure SMTP before retrying this notice."
        await db.commit()
        return {"status": "blocked"}
    sender, recipient, subject, body = (
        message.from_email or settings.smtp_from,
        message.to_email,
        message.subject,
        message.body,
    )
    message.status = "sending"
    await db.commit()
    try:
        await run_in_threadpool(mailer._smtp_send, sender, recipient, subject, body, None)
    except _DEFINITE_REJECTIONS:
        message.status = "failed"
        message.error = (
            "The mail relay rejected this notice. Check its configuration before retrying."
        )
    except Exception:
        message.status = "unknown"
        message.error = (
            "SMTP acceptance is uncertain. Check the relay log before requesting a resend."
        )
    else:
        message.status = "sent"
        message.sent_at = datetime.now(UTC)
        message.error = None
        current = await db.scalar(
            select(ProjectAssignment)
            .where(
                ProjectAssignment.org_id == org_id,
                ProjectAssignment.id == context["assignment_id"],
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current is not None and current.revision == context["revision"]:
            if context["category"] == "reminder":
                current.reminder_sent_at = message.sent_at
            elif context["category"] == "client_notice":
                current.client_notice_sent_at = message.sent_at
    await db.commit()
    return {"status": message.status}


async def retry(db: AsyncSession, row: ProjectAssignment, message_id: str) -> EmailMessage:
    message = await db.scalar(
        select(EmailMessage)
        .where(
            EmailMessage.org_id == row.org_id,
            EmailMessage.id == message_id,
            EmailMessage.command_key.startswith(prefix(row.id, row.revision)),
            EmailMessage.kind == "assignment",
            ~EmailMessage.command_key.contains(":reassigned:"),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if message is None:
        raise ValueError("This notice cannot be retried for the current assignment revision.")
    if message.status == "queued":
        return message
    if message.status not in {"blocked", "failed"}:
        raise ValueError(
            "Uncertain or accepted email cannot be resent automatically. Check the relay log."
        )
    # The context is recoverable only from the durable original job. A blocked
    # no-relay intent has no job, so rebuild its fixed identity from the key.
    category = (
        (message.command_key or "").removeprefix(prefix(row.id, row.revision)).split(":", 1)[0]
    )
    if category not in {"assigned", "changed", "cancelled"}:
        raise ValueError("Only the current assignee's notice can be retried here.")
    context = {
        "assignment_id": row.id,
        "revision": row.revision,
        "category": category,
        "recipient_user_id": row.assignee_user_id,
    }
    if message.command_hash != _fingerprint(message, context) or not await _eligible(
        db, row, message, context
    ):
        raise ValueError("The assignment or recipient changed. Review the current assignment.")
    if not settings.smtp_enabled:
        raise ValueError("Email relay is not configured. Configure SMTP before retrying.")
    message.status, message.error = "queued", None
    await jobs.enqueue(
        db,
        KIND,
        {"message_id": message.id, **context},
        org_id=row.org_id,
        idempotency_key=f"{message.id}:{uuid.uuid4().hex}",
        commit=False,
    )
    return message


async def status(db: AsyncSession, row: ProjectAssignment) -> str | None:
    if not row.notify_assignee:
        return "off"
    return await db.scalar(
        select(EmailMessage.status)
        .where(
            EmailMessage.org_id == row.org_id,
            EmailMessage.command_key.startswith(prefix(row.id, row.revision)),
            EmailMessage.kind == "assignment",
            ~EmailMessage.command_key.contains(":reassigned:"),
        )
        .order_by(EmailMessage.created_at.desc())
        .limit(1)
    )
