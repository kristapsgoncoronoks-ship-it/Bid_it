"""The client portal (WO-I, crm-module-research Part 3).

The token IS the credential (magic link — the segment's dominant model).
Two halves live here:

- **Management** (authenticated side): issue / regenerate / revoke a
  customer's portal link. Regenerating revokes every prior live token in
  the same breath — the old URL dies the moment a new one exists.
- **The portal read/write** (public side): resolve the token UNSCOPED
  (email-intake/calendar-feed pattern), then serve exactly that customer's
  world — their offers (with the quote-viewed stamp), their invoices with
  status, and ONLY the project documents someone explicitly shared. An
  unknown or revoked token 404s with nothing to enumerate.

Deciding an offer from the portal rides the EXISTING transition machinery
(sent → accepted | rejected) with the actor recorded as the customer —
one lifecycle, no portal-only side door. Services never commit.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.costing import Project
from app.models.customer import Customer
from app.models.customer_portal_token import CustomerPortalToken
from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.models.project_link import ProjectDocument
from app.models.project_offer import ProjectOffer
from app.services import project_offers
from app.services.project_profit import ProjectProfitError


class PortalError(Exception):
    """Base for portal failures the route maps to HTTP."""


class NotFoundError(PortalError):
    """Unknown/revoked token or foreign id — indistinguishable (§4.4)."""


# --------------------------------------------------------------------------- #
# Management (authenticated)
# --------------------------------------------------------------------------- #


async def _customer(db: AsyncSession, org_id: str, customer_id: str) -> Customer:
    row = await db.scalar(
        select(Customer).where(Customer.org_id == org_id, Customer.id == customer_id)
    )
    if row is None:
        raise NotFoundError("customer not found")
    return row


async def _live_token(
    db: AsyncSession, org_id: str, customer_id: str
) -> CustomerPortalToken | None:
    return await db.scalar(
        select(CustomerPortalToken)
        .where(
            CustomerPortalToken.org_id == org_id,
            CustomerPortalToken.customer_id == customer_id,
            CustomerPortalToken.revoked_at.is_(None),
        )
        .order_by(CustomerPortalToken.created_at.desc())
    )


async def get_or_create_link(
    db: AsyncSession, org_id: str, customer_id: str, *, created_by: str | None
) -> CustomerPortalToken:
    await _customer(db, org_id, customer_id)
    live = await _live_token(db, org_id, customer_id)
    if live is not None:
        return live
    row = CustomerPortalToken(
        org_id=org_id,
        customer_id=customer_id,
        token=secrets.token_urlsafe(32),
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def regenerate_link(
    db: AsyncSession, org_id: str, customer_id: str, *, created_by: str | None
) -> CustomerPortalToken:
    """Revoke every live token, issue a fresh one — the old URL dies now."""
    await revoke_link(db, org_id, customer_id)
    row = CustomerPortalToken(
        org_id=org_id,
        customer_id=customer_id,
        token=secrets.token_urlsafe(32),
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def revoke_link(db: AsyncSession, org_id: str, customer_id: str) -> int:
    await _customer(db, org_id, customer_id)
    now = datetime.now(UTC)
    revoked = 0
    for row in await db.scalars(
        select(CustomerPortalToken).where(
            CustomerPortalToken.org_id == org_id,
            CustomerPortalToken.customer_id == customer_id,
            CustomerPortalToken.revoked_at.is_(None),
        )
    ):
        row.revoked_at = now
        revoked += 1
    await db.flush()
    return revoked


# --------------------------------------------------------------------------- #
# The portal (public; token = credential)
# --------------------------------------------------------------------------- #


async def resolve_token(db: AsyncSession, token: str) -> tuple[str, str] | None:
    """(org_id, customer_id) for a LIVE token — UNSCOPED, for the public
    route, which then queries under the resolved tenant."""
    row = await db.scalar(
        select(CustomerPortalToken).where(
            CustomerPortalToken.token == token, CustomerPortalToken.revoked_at.is_(None)
        )
    )
    return (row.org_id, row.customer_id) if row else None


async def _customer_projects(db: AsyncSession, org_id: str, customer_id: str) -> dict[str, Project]:
    return {
        p.id: p
        for p in await db.scalars(
            select(Project).where(Project.org_id == org_id, Project.customer_id == customer_id)
        )
    }


async def summary(db: AsyncSession, org_id: str, customer_id: str) -> dict:
    """Everything the portal shows, in one payload — and the quote-viewed
    stamp: rendering the portal marks every yet-unviewed SENT offer as seen
    (first view only; the stamp never moves again)."""
    customer = await _customer(db, org_id, customer_id)
    org = await db.scalar(select(Organization).where(Organization.id == org_id))
    projects = await _customer_projects(db, org_id, customer_id)

    offers_out: list[dict] = []
    if projects:
        now = datetime.now(UTC)
        for o in await db.scalars(
            select(ProjectOffer)
            .where(ProjectOffer.org_id == org_id, ProjectOffer.project_id.in_(projects))
            .order_by(ProjectOffer.number, ProjectOffer.version.desc())
        ):
            if o.status == "superseded":
                continue  # history stays internal; the client sees live versions
            if o.status == "sent" and o.viewed_at is None:
                o.viewed_at = now
            offers_out.append(
                {
                    "offer_id": o.id,
                    "number": o.number,
                    "version": o.version,
                    "title": o.title,
                    "status": o.status,
                    "total": str(o.total),
                    "currency": o.currency,
                    "project": projects[o.project_id].name,
                    "lines": json.loads(o.line_items_json or "[]"),
                    "decidable": o.status == "sent",
                }
            )

    invoices_out = [
        {
            "number": inv.number or "draft",
            "total": str(Decimal(inv.total)),
            "currency": inv.currency,
            "status": inv.lifecycle,
            "issued_at": inv.issued_at.isoformat() if inv.issued_at else None,
        }
        for inv in await db.scalars(
            select(IssuedInvoice)
            .where(IssuedInvoice.org_id == org_id, IssuedInvoice.customer_id == customer_id)
            .order_by(IssuedInvoice.created_at.desc())
        )
        if inv.lifecycle != "draft"  # unissued work is not the client's yet
    ]

    documents_out = []
    if projects:
        for d in await db.scalars(
            select(ProjectDocument)
            .where(
                ProjectDocument.org_id == org_id,
                ProjectDocument.project_id.in_(projects),
                ProjectDocument.shared_with_customer.is_(True),
            )
            .order_by(ProjectDocument.created_at.desc())
        ):
            documents_out.append(
                {
                    "document_id": d.id,
                    "filename": d.filename,
                    "kind": d.kind,
                    "project": projects[d.project_id].name,
                }
            )

    await db.flush()
    return {
        "organization": org.name if org else "",
        "customer": customer.name,
        "offers": offers_out,
        "invoices": invoices_out,
        "documents": documents_out,
    }


async def decide_offer(
    db: AsyncSession, org_id: str, customer_id: str, offer_id: str, decision: str
) -> ProjectOffer:
    """Accept or decline — through the ONE existing transition machinery,
    only for an offer that belongs to this customer and is actually with
    them (`sent`). Anything else 404s opaquely."""
    if decision not in ("accepted", "rejected"):
        raise PortalError("decision must be 'accepted' or 'rejected'")
    projects = await _customer_projects(db, org_id, customer_id)
    if not projects:
        raise NotFoundError("offer not found")
    offer = await db.scalar(
        select(ProjectOffer).where(
            ProjectOffer.org_id == org_id,
            ProjectOffer.id == offer_id,
            ProjectOffer.project_id.in_(projects),
        )
    )
    if offer is None or offer.status != "sent":
        raise NotFoundError("offer not found")
    if offer.viewed_at is None:
        offer.viewed_at = datetime.now(UTC)
    try:
        moved, _seeded = await project_offers.transition_offer(
            db, org_id, offer_id, decision, actor="customer (portal)"
        )
    except ProjectProfitError as exc:  # pragma: no cover - guarded above
        raise PortalError(str(exc)) from exc
    return moved


async def load_shared_document(
    db: AsyncSession, org_id: str, customer_id: str, document_id: str
) -> ProjectDocument:
    """The row only — the route loads bytes via the existing document store.
    SHARED documents of THIS customer's projects; everything else 404s."""
    projects = await _customer_projects(db, org_id, customer_id)
    if not projects:
        raise NotFoundError("document not found")
    row = await db.scalar(
        select(ProjectDocument).where(
            ProjectDocument.org_id == org_id,
            ProjectDocument.id == document_id,
            ProjectDocument.project_id.in_(projects),
            ProjectDocument.shared_with_customer.is_(True),
        )
    )
    if row is None:
        raise NotFoundError("document not found")
    return row
