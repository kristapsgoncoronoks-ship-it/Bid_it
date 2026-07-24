"""Overdue-invoice dunning — a configurable escalation LADDER (Phase 16).

Each level fires once the invoice is `days_overdue` past due, with an escalating
tone, and fires AT MOST ONCE (tracked by `issued_invoices.dunning_level`) — so the
daily job escalates a debtor through the ladder instead of re-sending the same
reminder every day. The ladder is per-tenant (`dunning_policies`); with no rows an
org uses `DEFAULT_LADDER`. Extracted from the issued route so the SAME logic runs on
demand (a button) and on a schedule. Tenant-scoped; safe to run repeatedly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.dunning_policy import DunningPolicy
from app.models.email_message import EmailMessage
from app.models.issued_invoice import IssuedInvoice
from app.services import audit, issued_status, mailer


@dataclass(frozen=True)
class DunningLevel:
    level: int
    days_overdue: int
    tone: str


# The built-in ladder used when an org has configured no `dunning_policies` rows:
# a gentle reminder at 3 days, a firm notice at 14, a final notice at 30.
DEFAULT_LADDER: tuple[DunningLevel, ...] = (
    DunningLevel(1, 3, "reminder"),
    DunningLevel(2, 14, "firm"),
    DunningLevel(3, 30, "final"),
)


async def load_ladder(db: AsyncSession, org_id: str) -> list[DunningLevel]:
    """The org's active ladder, ordered by level. No configured rows → the default
    ladder; rows present but all inactive → an empty ladder (dunning disabled)."""
    rows = list(
        await db.scalars(
            select(DunningPolicy)
            .where(DunningPolicy.org_id == org_id)
            .order_by(DunningPolicy.level)
        )
    )
    if not rows:
        return list(DEFAULT_LADDER)
    return [DunningLevel(r.level, r.days_overdue, r.tone) for r in rows if r.active]


def resolve_level(days_overdue: int, ladder: list[DunningLevel]) -> DunningLevel | None:
    """The highest ladder level whose threshold this invoice has reached (or None)."""
    eligible = [lv for lv in ladder if days_overdue >= lv.days_overdue]
    return max(eligible, key=lambda lv: lv.level) if eligible else None


def _seller_name(inv: IssuedInvoice) -> str:
    import json

    seller = json.loads(inv.seller_json)
    return seller.get("legal_name") or seller.get("trade_name") or "Us"


async def send_reminder(
    db: AsyncSession,
    org_id: str,
    inv: IssuedInvoice,
    recipient: str,
    *,
    tone: str = "reminder",
    level: int | None = None,
    today: date | None = None,
) -> EmailMessage:
    days = issued_status.days_overdue_of(inv, today)
    subject, text = mailer.reminder_email(
        seller_name=_seller_name(inv),
        number=inv.number,
        buyer_name=inv.buyer_name,
        currency=inv.currency,
        outstanding=issued_status.outstanding_of(inv),
        days_overdue=days,
        penalty=issued_status.penalty_of(inv, today),
        due_date=inv.due_date,
        penalty_rate=inv.penalty_rate,
        tone=tone,
    )
    msg = await mailer.send(
        db,
        org_id,
        kind="reminder",
        to_email=recipient,
        subject=subject,
        body=text,
        invoice_id=inv.id,
    )
    inv.reminder_count = (inv.reminder_count or 0) + 1
    inv.last_reminder_at = today or date.today()
    if level is not None and level > (inv.dunning_level or 0):
        inv.dunning_level = level
    await audit.record(
        db,
        audit.A.ISSUED_REMINDER,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"number": inv.number, "to": recipient, "days_overdue": days, "level": level},
    )
    return msg


@dataclass
class DunningResult:
    sent: int = 0
    skipped_no_email: int = 0
    skipped: int = 0  # overdue but no new level is due (below threshold / already fired)
    messages: list[EmailMessage] = field(default_factory=list)


async def run_overdue(db: AsyncSession, org_id: str, today: date | None = None) -> DunningResult:
    """Walk the ladder: for every overdue invoice with a customer email, send the
    highest eligible level that has not yet fired, and advance its `dunning_level`."""
    today = today or date.today()
    ladder = await load_ladder(db, org_id)
    rows = list(
        await db.scalars(
            select(IssuedInvoice)
            .where(IssuedInvoice.org_id == org_id)
            .options(selectinload(IssuedInvoice.lines))
        )
    )
    res = DunningResult()
    for inv in rows:
        if issued_status.status_of(inv, today) != issued_status.OVERDUE:
            continue
        if not inv.buyer_email:
            res.skipped_no_email += 1
            continue
        target = resolve_level(issued_status.days_overdue_of(inv, today), ladder)
        if target is None or (inv.dunning_level or 0) >= target.level:
            res.skipped += 1
            continue
        res.messages.append(
            await send_reminder(
                db,
                org_id,
                inv,
                inv.buyer_email,
                tone=target.tone,
                level=target.level,
                today=today,
            )
        )
        res.sent += 1
    return res
