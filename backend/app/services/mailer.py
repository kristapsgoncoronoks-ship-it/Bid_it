"""Outbound email — invoice delivery and payment reminders.

Every send is RECORDED to the `email_messages` outbox first, so there is an
auditable sent-mail history whether or not an SMTP relay is configured. When
`settings.smtp_host` is set the message is also delivered over SMTP (in a
threadpool, off the event loop); otherwise it stays 'recorded'. A delivery
failure is captured on the row (status='failed') and NEVER raised — sending an
invoice must not 500 the request. No secrets are stored; only the text body.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage as MimeEmail

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.email_message import EmailMessage

log = logging.getLogger("invoiceiq.mailer")


def _smtp_send(
    from_email: str, to_email: str, subject: str, body: str, attachment: tuple[str, bytes] | None
) -> None:
    if not settings.smtp_host:
        raise RuntimeError("SMTP host not configured")
    msg = MimeEmail()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment is not None:
        name, data = attachment
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
        if settings.smtp_starttls:
            s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password or "")
        s.send_message(msg)


async def send(
    db: AsyncSession,
    org_id: str,
    *,
    kind: str,
    to_email: str,
    subject: str,
    body: str,
    from_email: str | None = None,
    invoice_id: str | None = None,
    attachment: tuple[str, bytes] | None = None,
) -> EmailMessage:
    """Record (and, if SMTP is configured, deliver) one message. Never raises."""
    row = EmailMessage(
        org_id=org_id,
        invoice_id=invoice_id,
        kind=kind,
        to_email=to_email,
        subject=subject,
        body=body,
        status="recorded",
    )
    sender = from_email or settings.smtp_from
    if settings.smtp_enabled:
        try:
            await run_in_threadpool(_smtp_send, sender, to_email, subject, body, attachment)
            row.status = "sent"
            row.sent_at = datetime.now(UTC)
        except Exception as exc:  # delivery must never break the operation
            log.warning("SMTP send to %s failed: %s", to_email, exc)
            row.status = "failed"
            row.error = str(exc)[:500]
    db.add(row)
    return row


async def list_messages(
    db: AsyncSession,
    org_id: str,
    *,
    invoice_id: str | None = None,
    kinds: tuple[str, ...] | None = None,
    limit: int = 100,
) -> list[EmailMessage]:
    stmt = select(EmailMessage).where(EmailMessage.org_id == org_id)
    if invoice_id:
        stmt = stmt.where(EmailMessage.invoice_id == invoice_id)
    if kinds:
        stmt = stmt.where(EmailMessage.kind.in_(kinds))
    return list(await db.scalars(stmt.order_by(EmailMessage.created_at.desc()).limit(limit)))


def ap_digest_email(
    *,
    due_soon_count: int,
    due_soon_amount,
    overdue_count: int,
    overdue_amount,
    currency: str = "EUR",
) -> tuple[str, str]:
    """A payables due-date digest (Phase 16b): supplier invoices due soon / overdue."""
    subject = f"Payables due: {overdue_count} overdue, {due_soon_count} due soon"
    lines = ["Some supplier invoices need attention:", ""]
    if overdue_count:
        lines.append(f"- {overdue_count} overdue, totalling {_fmt(overdue_amount, currency)}")
    if due_soon_count:
        lines.append(
            f"- {due_soon_count} due within a week, totalling {_fmt(due_soon_amount, currency)}"
        )
    lines += ["", "Review and schedule them from the Cash position dashboard.", ""]
    return subject, "\n".join(lines)


# Invoice-delivery message kinds (the /issued/emails log shows only these — not
# auth/verification/reset mail that shares the same outbox table).
INVOICE_MAIL_KINDS: tuple[str, ...] = ("invoice", "reminder")


# --- Message templates ---------------------------------------------------------


def _fmt(amount, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def archive_expiry_email(
    *,
    count: int,
    earliest,
    notice_days: int,
    retention_years: int,
    examples: list[tuple[str, str]],
) -> tuple[str, str]:
    """The pre-expiry notice. Plain text, no urgency theatre — the reader is a
    company owner deciding whether to extend or to download, and the email's one
    job is that neither deadline nor option is a surprise. The examples give the
    reader something concrete to recognise; the archive screen has the full list."""
    when = earliest.date().isoformat() if hasattr(earliest, "date") else str(earliest)
    plural = "records" if count != 1 else "record"
    lines = [
        f"{count} archived invoice {plural} will be permanently removed from your",
        f"archive, the earliest on {when}.",
        "",
        f"Archived invoices are kept for {retention_years} years after deletion and",
        f"this notice is sent {notice_days} days before the first removal, so you can:",
        "",
        "  - download the records or their documents from the Archive screen, or",
        "  - ask us about extending your archive retention period.",
        "",
        "For example:",
    ]
    lines += [f"  - {num}" + (f" ({vendor})" if vendor else "") for num, vendor in examples]
    lines += [
        "",
        "Nothing is removed before the date above. If your accountant requires these",
        "records to be kept longer, act before that date - removal is permanent.",
    ]
    subject = f"{count} archived {plural} due for removal - earliest {when}"
    return subject, "\n".join(lines)


def invoice_email(
    *, seller_name: str, number: str, buyer_name: str, total, currency: str, due_date
) -> tuple[str, str]:
    subject = f"Invoice {number} from {seller_name}"
    due = f" It is due on {due_date.isoformat()}." if due_date else ""
    body = (
        f"Dear {buyer_name},\n\n"
        f"Please find attached invoice {number} for {_fmt(total, currency)}.{due}\n\n"
        f"Thank you for your business.\n\n"
        f"Kind regards,\n{seller_name}"
    )
    return subject, body


# Dunning-ladder tone → (subject label, opening line, closing line). The escalation
# is language-only; the figures are identical. `firm`/`final` are later levels.
_REMINDER_TONES: dict[str, tuple[str, str, str]] = {
    "reminder": (
        "Payment reminder",
        "Our records show invoice {number} remains unpaid and is now {days} day(s) "
        "past its due date{due}.",
        "Please arrange payment at your earliest convenience. If you have already "
        "paid, kindly disregard this notice.",
    ),
    "firm": (
        "Overdue notice",
        "Invoice {number} is now {days} day(s) overdue{due} and remains unpaid "
        "despite our earlier reminder.",
        "We must ask that you settle this balance without further delay to avoid "
        "any interruption to your account.",
    ),
    "final": (
        "FINAL NOTICE",
        "Invoice {number} remains unpaid and is now {days} day(s) overdue{due}, "
        "despite previous reminders.",
        "Unless payment is received within 7 days, this account may be referred for "
        "collection. Please treat this as a matter of urgency.",
    ),
}


def reminder_email(
    *,
    seller_name: str,
    number: str,
    buyer_name: str,
    currency: str,
    outstanding,
    days_overdue: int,
    penalty,
    due_date,
    penalty_rate,
    tone: str = "reminder",
) -> tuple[str, str]:
    total_due = outstanding + penalty
    label, opening, closing = _REMINDER_TONES.get(tone, _REMINDER_TONES["reminder"])
    due = f" of {due_date.isoformat()}" if due_date else ""
    subject = f"{label}: invoice {number} ({days_overdue} days overdue)"
    lines = [
        f"Dear {buyer_name},",
        "",
        opening.format(number=number, days=days_overdue, due=due),
        "",
        f"Outstanding balance: {_fmt(outstanding, currency)}",
    ]
    if penalty and penalty > 0:
        lines.append(f"Late-payment interest ({penalty_rate}% p.a.): {_fmt(penalty, currency)}")
        lines.append(f"Total now due: {_fmt(total_due, currency)}")
    lines += ["", closing, "", f"Kind regards,\n{seller_name}"]
    return subject, "\n".join(lines)
