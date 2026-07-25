"""AP due-date digest (Phase 16b).

A daily per-tenant email summarising supplier invoices that are due soon or already
overdue, so payables don't slip. Reuses the email outbox; skips silently when
nothing is due or no issuer email is configured. Tenant-scoped.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.issuer import IssuerProfile
from app.services import ap_aging, mailer


async def send_digest(db: AsyncSession, org_id: str, today: date | None = None) -> dict:
    """Email the org's issuer a due-soon/overdue payables digest. No-ops (sent=0)
    when nothing is due or no issuer email is set."""
    s = await ap_aging.summary(db, org_id, today)
    if s.due_soon_count == 0 and s.overdue_count == 0:
        return {"sent": 0, "due_soon": 0, "overdue": 0}
    issuer = await db.scalar(select(IssuerProfile).where(IssuerProfile.org_id == org_id))
    recipient = issuer.email if issuer else None
    if not recipient:
        return {
            "sent": 0,
            "skipped_no_email": 1,
            "due_soon": s.due_soon_count,
            "overdue": s.overdue_count,
        }
    subject, body = mailer.ap_digest_email(
        due_soon_count=s.due_soon_count,
        due_soon_amount=s.due_soon_amount,
        overdue_count=s.overdue_count,
        overdue_amount=s.overdue_amount,
        # The digest's totals are single-currency (WO-8) — label them honestly.
        currency=s.currency,
    )
    await mailer.send(db, org_id, kind="ap_alert", to_email=recipient, subject=subject, body=body)
    return {"sent": 1, "due_soon": s.due_soon_count, "overdue": s.overdue_count}
