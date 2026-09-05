"""WO-P (R19) — the getting-started checklist, DERIVED from existing rows.

The demo seed papers over the "empty workspace, now what?" gap; a real new
workspace hits it immediately. This service computes the setup path the plan
names — issuer profile → modules → team → first customer → first invoice —
from state the org already has, so a step can never disagree with reality and
nothing needs back-filling for the orgs that predate it.

The ONE persisted bit is `organizations.onboarding_dismissed_at` (an admin
closed the card for the whole workspace). Everything else re-derives on every
read: finish a step through its own screen and the checklist simply notices.

Step semantics, deliberately loose on purpose:
- "team": a second member OR a pending invitation counts — the setup act is
  INVITING; whether the invitee accepted is not this card's business.
- "customer": a Partner or a Customer row counts — both screens create a
  counterparty you can bill.
- "invoice": any supplier invoice OR any issued invoice — the first document
  through EITHER side proves the org is working, and which side comes first
  depends on the business.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.invitation import Invitation
from app.models.invoice import Invoice
from app.models.issued_invoice import IssuedInvoice
from app.models.issuer import IssuerProfile
from app.models.module import OrgModule
from app.models.organization import Organization
from app.models.partner import Partner
from app.models.user import User


async def _exists(db: AsyncSession, stmt) -> bool:
    return (await db.scalar(stmt.limit(1))) is not None


async def checklist(db: AsyncSession, org: Organization) -> dict:
    """The composed card: ordered steps with done flags, plus the two facts the
    SPA needs to decide whether to render it at all."""
    org_id = org.id

    issuer_done = await _exists(db, select(IssuerProfile.id).where(IssuerProfile.org_id == org_id))
    modules_done = await _exists(
        db,
        select(OrgModule.id).where(OrgModule.org_id == org_id, OrgModule.enabled.is_(True)),
    )
    members = await db.scalar(select(func.count()).select_from(User).where(User.org_id == org_id))
    # PROD-011 (audit 2026-09-05): "a pending invitation counts" — pending, not
    # any row. An accepted invitation is represented by the member it created
    # (counted above); an expired one is an invite nobody can act on any more.
    # Before this, one expired invite ticked the step for good.
    now = datetime.now(UTC)
    team_done = (members or 0) > 1 or await _exists(
        db,
        select(Invitation.id).where(
            Invitation.org_id == org_id,
            Invitation.accepted.is_(False),
            (Invitation.expires_at.is_(None)) | (Invitation.expires_at > now),
        ),
    )
    customer_done = await _exists(
        db, select(Partner.id).where(Partner.org_id == org_id)
    ) or await _exists(db, select(Customer.id).where(Customer.org_id == org_id))
    invoice_done = await _exists(
        db, select(Invoice.id).where(Invoice.org_id == org_id)
    ) or await _exists(db, select(IssuedInvoice.id).where(IssuedInvoice.org_id == org_id))

    steps = [
        {
            "key": "issuer",
            "label": "Set up your company profile",
            "detail": "Name, VAT number and bank details — every issued document carries them.",
            "href": "/issuer",
            "done": issuer_done,
        },
        {
            "key": "modules",
            "label": "Choose your modules",
            "detail": "Turn on the parts of the platform this workspace will use.",
            "href": "/settings",
            "done": modules_done,
        },
        {
            "key": "team",
            "label": "Invite your team",
            "detail": "Invite a colleague — roles keep duties separated from day one.",
            # PROD-011: Team lives at /team (App.tsx); /settings has no invite form.
            "href": "/team",
            "done": team_done,
        },
        {
            "key": "customer",
            "label": "Add your first customer",
            "detail": "A counterparty to bill — workflow presets live on the customer.",
            "href": "/partners",
            "done": customer_done,
        },
        {
            "key": "invoice",
            "label": "Process your first invoice",
            "detail": "Upload a supplier invoice or issue your first customer invoice.",
            "href": "/upload",
            "done": invoice_done,
        },
    ]
    return {
        "steps": steps,
        "done_count": sum(1 for s in steps if s["done"]),
        "complete": all(s["done"] for s in steps),
        "dismissed": org.onboarding_dismissed_at is not None,
    }


def dismiss(org: Organization) -> None:
    """Stamp the org-wide dismissal (idempotent — a second dismiss keeps the
    first stamp, so the audit trail's first event stays the true one)."""
    if org.onboarding_dismissed_at is None:
        org.onboarding_dismissed_at = datetime.now(UTC)
