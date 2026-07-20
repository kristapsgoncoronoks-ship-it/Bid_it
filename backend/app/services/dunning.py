"""Overdue-invoice dunning: send a payment reminder for every overdue invoice.

Extracted from the issued route so the SAME logic runs on demand (a button) and
on a schedule (a background job). Tenant-scoped; safe to run repeatedly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.email_message import EmailMessage
from app.models.issued_invoice import IssuedInvoice
from app.services import audit, issued_status, mailer


def _seller_name(inv: IssuedInvoice) -> str:
    import json

    seller = json.loads(inv.seller_json)
    return seller.get("legal_name") or seller.get("trade_name") or "Us"


async def send_reminder(db: AsyncSession, org_id: str, inv: IssuedInvoice, recipient: str) -> EmailMessage:
    subject, text = mailer.reminder_email(
        seller_name=_seller_name(inv), number=inv.number, buyer_name=inv.buyer_name,
        currency=inv.currency, outstanding=issued_status.outstanding_of(inv),
        days_overdue=issued_status.days_overdue_of(inv), penalty=issued_status.penalty_of(inv),
        due_date=inv.due_date, penalty_rate=inv.penalty_rate,
    )
    msg = await mailer.send(db, org_id, kind="reminder", to_email=recipient, subject=subject,
                            body=text, invoice_id=inv.id)
    inv.reminder_count = (inv.reminder_count or 0) + 1
    inv.last_reminder_at = date.today()
    await audit.record(db, audit.A.ISSUED_REMINDER, target_type="issued_invoice", target_id=inv.id,
                       meta={"number": inv.number, "to": recipient,
                             "days_overdue": issued_status.days_overdue_of(inv)})
    return msg


@dataclass
class DunningResult:
    sent: int = 0
    skipped_no_email: int = 0
    messages: list[EmailMessage] = field(default_factory=list)


async def run_overdue(db: AsyncSession, org_id: str) -> DunningResult:
    """Send a reminder for every overdue invoice that has a customer email."""
    rows = list(await db.scalars(
        select(IssuedInvoice)
        .where(IssuedInvoice.org_id == org_id)
        .options(selectinload(IssuedInvoice.lines))
    ))
    res = DunningResult()
    for inv in rows:
        if issued_status.status_of(inv) != issued_status.OVERDUE:
            continue
        if not inv.buyer_email:
            res.skipped_no_email += 1
            continue
        res.messages.append(await send_reminder(db, org_id, inv, inv.buyer_email))
        res.sent += 1
    return res
