"""Recurring-invoice schedules: CRUD + the idempotent generator.

A schedule stores a frozen invoice template plus a cadence. `generate_due` is the
worker entrypoint: for every active schedule whose `next_run_date` has arrived it
materialises a real issued invoice and advances the schedule — in ONE transaction
per invoice, so a re-run never duplicates an occurrence it has already passed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.issued_invoice import IssuedInvoice
from app.models.recurring_invoice import RecurringInvoice
from app.schemas.issued import IssuedInvoiceCreate
from app.services import audit, issued_service
from app.services import issuer as issuer_svc
from app.services import partners as partners_svc

FREQUENCIES = ("weekly", "monthly", "quarterly", "yearly")

# Safety cap: how many invoices a single schedule may emit in one sweep, so a
# schedule that has been paused for years can't flood on the first run.
_CATCHUP_CAP = 24


def advance(d: date, frequency: str, interval: int) -> date:
    """Next occurrence after `d`, `interval` periods of `frequency` later."""
    interval = max(1, interval)
    if frequency == "weekly":
        return _add_days(d, 7 * interval)
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(frequency, 1) * interval
    return _add_months(d, months)


def _add_days(d: date, days: int) -> date:
    from datetime import timedelta

    return d + timedelta(days=days)


def _add_months(d: date, months: int) -> date:
    """Add whole months, clamping the day to the target month's last day."""
    m0 = d.month - 1 + months
    year = d.year + m0 // 12
    month = m0 % 12 + 1
    # Last valid day of the target month.
    if month == 12:
        last = 31
    else:
        from datetime import date as _date

        last = (_date(year + (month // 12), month % 12 + 1, 1) - _date(year, month, 1)).days
    return date(year, month, min(d.day, last))


def template_from_create(body: IssuedInvoiceCreate) -> dict:
    """The invoice fields we freeze on a schedule (no per-occurrence dates)."""
    data = body.model_dump(mode="json", exclude_none=False)
    for k in ("issue_date", "due_date"):
        data.pop(k, None)
    return data


def _body_from_template(rec: RecurringInvoice) -> IssuedInvoiceCreate:
    return IssuedInvoiceCreate.model_validate(json.loads(rec.template_json))


@dataclass
class GenerationResult:
    generated: list[tuple[str, str]]  # (recurring_id, invoice_number)


async def create(
    db: AsyncSession,
    org_id: str,
    *,
    body: IssuedInvoiceCreate,
    frequency: str,
    interval: int,
    start_date: date,
    end_date: date | None,
    title: str | None,
    payment_terms_days: int | None,
) -> RecurringInvoice:
    rec = RecurringInvoice(
        org_id=org_id,
        partner_id=body.partner_id,
        title=title,
        template_json=json.dumps(template_from_create(body)),
        frequency=frequency,
        interval=max(1, interval),
        payment_terms_days=payment_terms_days,
        start_date=start_date,
        next_run_date=start_date,
        end_date=end_date,
        active=True,
    )
    db.add(rec)
    await audit.record(
        db,
        audit.A.RECURRING_CREATE,
        target_type="recurring_invoice",
        target_id=None,
        meta={"title": title or "", "frequency": frequency},
    )
    await db.commit()
    await db.refresh(rec)
    return rec


async def list_all(db: AsyncSession, org_id: str) -> list[RecurringInvoice]:
    return list(
        await db.scalars(
            select(RecurringInvoice)
            .where(RecurringInvoice.org_id == org_id)
            .order_by(RecurringInvoice.created_at.desc())
        )
    )


async def get(db: AsyncSession, org_id: str, rec_id: str) -> RecurringInvoice | None:
    return await db.scalar(
        select(RecurringInvoice).where(
            RecurringInvoice.id == rec_id, RecurringInvoice.org_id == org_id
        )
    )


async def _generate_one(
    db: AsyncSession, rec: RecurringInvoice, profile, on: date
) -> IssuedInvoice:
    body = _body_from_template(rec)
    partner = None
    if body.partner_id:
        partner = await partners_svc.get_partner(db, rec.org_id, body.partner_id)
    inv = issued_service.build_invoice(
        profile,
        body,
        org_id=rec.org_id,
        partner=partner,
        issue_date=on,
        payment_terms_days=rec.payment_terms_days,
    )
    db.add(inv)
    rec.last_generated_at = on
    rec.generated_count += 1
    return inv


async def generate_due(
    db: AsyncSession, org_id: str, *, today: date | None = None
) -> GenerationResult:
    """Materialise every occurrence due on/before `today` for this tenant."""
    today = today or date.today()
    schedules = list(
        await db.scalars(
            select(RecurringInvoice).where(
                RecurringInvoice.org_id == org_id,
                RecurringInvoice.active.is_(True),
                RecurringInvoice.next_run_date <= today,
            )
        )
    )
    if not schedules:
        return GenerationResult(generated=[])

    profile = await issuer_svc.get_or_create(db, org_id)
    generated: list[tuple[str, str]] = []
    for rec in schedules:
        emitted = 0
        while (
            rec.next_run_date <= today
            and (rec.end_date is None or rec.next_run_date <= rec.end_date)
            and emitted < _CATCHUP_CAP
        ):
            inv = await _generate_one(db, rec, profile, rec.next_run_date)
            await db.flush()  # assign number before the next iteration reads it
            generated.append((rec.id, inv.number))
            rec.next_run_date = advance(rec.next_run_date, rec.frequency, rec.interval)
            emitted += 1
        # Auto-close a schedule that has run past its end date.
        if rec.end_date is not None and rec.next_run_date > rec.end_date:
            rec.active = False
        if emitted:
            await audit.record(
                db,
                audit.A.RECURRING_GENERATE,
                target_type="recurring_invoice",
                target_id=rec.id,
                meta={"count": emitted},
            )
    await db.commit()
    return GenerationResult(generated=generated)
