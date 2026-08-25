"""CRM light (WO-H, docs/design/crm-module-research.md Part 2).

Three read/write surfaces, all deliberately thin:

- **Notes**: the only hand-written part of the customer timeline.
- **Lifecycle**: prospect | active | dormant | lost — a stage attribute on
  the customer record. No lead entity (documented anti-pattern).
- **Timeline**: DERIVED, never curated — one reverse-chronological merge of
  notes, offers born/moved (stage events via the customer's projects),
  invoices issued to the customer, and emails actually sent to the
  customer's address. If it happened, it shows; nobody "maintains" it.
- **Pipeline**: the kanban read over the EXISTING offer pipeline — offers
  grouped by status with days-in-stage from the stage history and a
  staleness flag on quiet `sent` offers (the "rotting" signal the research
  singled out as the one pipeline feature this segment actually uses).

Services never commit; routes commit mutation + audit together.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.costing import Project
from app.models.crm import CUSTOMER_LIFECYCLES, CustomerNote, OfferStageEvent
from app.models.customer import Customer
from app.models.email_message import EmailMessage
from app.models.issued_invoice import IssuedInvoice
from app.models.project_offer import ProjectOffer

#: A `sent` offer with no movement for this many days is flagged stale.
STALE_AFTER_DAYS = 14
TIMELINE_LIMIT = 100


class CrmError(Exception):
    """Base for CRM failures the route maps to HTTP."""


class NotFoundError(CrmError):
    """Unknown (or other-tenant — indistinguishable, §4.4) id."""


def _as_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def _customer(db: AsyncSession, org_id: str, customer_id: str) -> Customer:
    row = await db.scalar(
        select(Customer).where(Customer.org_id == org_id, Customer.id == customer_id)
    )
    if row is None:
        raise NotFoundError("customer not found")
    return row


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #


async def add_note(
    db: AsyncSession, org_id: str, customer_id: str, *, body: str, created_by: str | None
) -> CustomerNote:
    await _customer(db, org_id, customer_id)
    text = body.strip()
    if not text:
        raise CrmError("An empty note says nothing")
    row = CustomerNote(org_id=org_id, customer_id=customer_id, body=text, created_by=created_by)
    db.add(row)
    await db.flush()
    return row


async def list_notes(db: AsyncSession, org_id: str, customer_id: str) -> list[CustomerNote]:
    await _customer(db, org_id, customer_id)
    return list(
        await db.scalars(
            select(CustomerNote)
            .where(CustomerNote.org_id == org_id, CustomerNote.customer_id == customer_id)
            .order_by(CustomerNote.created_at.desc(), CustomerNote.id)
        )
    )


async def delete_note(db: AsyncSession, org_id: str, customer_id: str, note_id: str) -> dict:
    """Returns what was destroyed — the route's audit meta needs it."""
    row = await db.scalar(
        select(CustomerNote).where(
            CustomerNote.org_id == org_id,
            CustomerNote.customer_id == customer_id,
            CustomerNote.id == note_id,
        )
    )
    if row is None:
        raise NotFoundError("note not found")
    destroyed = {"note_id": row.id, "body": row.body, "created_by": row.created_by}
    await db.delete(row)
    await db.flush()
    return destroyed


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


async def set_lifecycle(
    db: AsyncSession, org_id: str, customer_id: str, lifecycle: str
) -> tuple[Customer, str]:
    """Returns (customer, prior) — the audit records where it moved FROM."""
    if lifecycle not in CUSTOMER_LIFECYCLES:
        raise CrmError(f"Unknown lifecycle stage '{lifecycle}'")
    row = await _customer(db, org_id, customer_id)
    prior = row.lifecycle
    row.lifecycle = lifecycle
    await db.flush()
    return row, prior


# --------------------------------------------------------------------------- #
# The derived timeline
# --------------------------------------------------------------------------- #


def _event(at: datetime, kind: str, title: str, ref: str | None = None) -> dict:
    return {"at": _as_utc(at).isoformat(), "kind": kind, "title": title, "ref": ref}


async def timeline(db: AsyncSession, org_id: str, customer_id: str) -> list[dict]:
    customer = await _customer(db, org_id, customer_id)
    events: list[dict] = []

    for n in await list_notes(db, org_id, customer_id):
        who = f" — {n.created_by}" if n.created_by else ""
        events.append(_event(n.created_at, "note", f"{n.body}{who}"))

    projects = {
        p.id: p
        for p in await db.scalars(
            select(Project).where(Project.org_id == org_id, Project.customer_id == customer_id)
        )
    }
    if projects:
        offers = {
            o.id: o
            for o in await db.scalars(
                select(ProjectOffer).where(
                    ProjectOffer.org_id == org_id, ProjectOffer.project_id.in_(projects)
                )
            )
        }
        if offers:
            stage_events = await db.scalars(
                select(OfferStageEvent).where(
                    OfferStageEvent.org_id == org_id, OfferStageEvent.offer_id.in_(offers)
                )
            )
            for se in stage_events:
                o = offers[se.offer_id]
                label = "created" if se.from_status is None else se.to_status
                events.append(
                    _event(
                        se.created_at,
                        "offer",
                        f"Offer {o.number} v{o.version} {label}",
                        f"/projects/{o.project_id}",
                    )
                )
            # WO-I: the quote-viewed signal — the customer opened it in the
            # portal. A stamp on the offer, surfaced here, never a stage move.
            for o in offers.values():
                if o.viewed_at is not None:
                    events.append(
                        _event(
                            o.viewed_at,
                            "offer",
                            f"Offer {o.number} v{o.version} viewed by the customer",
                            f"/projects/{o.project_id}",
                        )
                    )
        for p in projects.values():
            events.append(
                _event(p.created_at, "project", f"Project {p.code} · {p.name}", f"/projects/{p.id}")
            )

    issued = await db.scalars(
        select(IssuedInvoice).where(
            IssuedInvoice.org_id == org_id, IssuedInvoice.customer_id == customer_id
        )
    )
    for inv in issued:
        label = inv.number or "draft"
        when = inv.issued_at or inv.created_at
        events.append(
            _event(
                when,
                "invoice",
                f"Invoice {label} — {inv.lifecycle}, {Decimal(inv.total):.2f} {inv.currency}",
                "/issue",
            )
        )

    if customer.email:
        mails = await db.scalars(
            select(EmailMessage).where(
                EmailMessage.org_id == org_id, EmailMessage.to_email == customer.email
            )
        )
        for m in mails:
            events.append(_event(m.created_at, "email", f"Email: {m.subject}"))

    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:TIMELINE_LIMIT]


# --------------------------------------------------------------------------- #
# The offer pipeline (kanban read)
# --------------------------------------------------------------------------- #


async def pipeline(db: AsyncSession, org_id: str) -> dict:
    """Offers grouped by status, each with its project/customer label,
    days-in-stage (from the latest stage event; updated_at as fallback for
    offers born before stage history existed) and the staleness flag."""
    offers = list(await db.scalars(select(ProjectOffer).where(ProjectOffer.org_id == org_id)))
    if not offers:
        return {"stale_after_days": STALE_AFTER_DAYS, "columns": {}}

    projects = {
        p.id: p
        for p in await db.scalars(
            select(Project).where(
                Project.org_id == org_id, Project.id.in_({o.project_id for o in offers})
            )
        )
    }
    customer_ids = {p.customer_id for p in projects.values() if p.customer_id}
    customers = (
        {
            c.id: c.name
            for c in await db.scalars(
                select(Customer).where(Customer.org_id == org_id, Customer.id.in_(customer_ids))
            )
        }
        if customer_ids
        else {}
    )
    latest_move: dict[str, datetime] = {}
    for se in await db.scalars(
        select(OfferStageEvent).where(
            OfferStageEvent.org_id == org_id,
            OfferStageEvent.offer_id.in_({o.id for o in offers}),
        )
    ):
        t = _as_utc(se.created_at)
        if se.offer_id not in latest_move or t > latest_move[se.offer_id]:
            latest_move[se.offer_id] = t

    now = datetime.now(UTC)
    columns: dict[str, list[dict]] = {}
    for o in offers:
        moved = latest_move.get(o.id, _as_utc(o.updated_at))
        days = max(0, (now - moved).days)
        project = projects.get(o.project_id)
        columns.setdefault(o.status, []).append(
            {
                "offer_id": o.id,
                "number": o.number,
                "version": o.version,
                "title": o.title,
                "total": str(o.total),
                "currency": o.currency,
                "project_id": o.project_id,
                "project": f"{project.code} · {project.name}" if project else "",
                "customer": (
                    customers.get(project.customer_id) if project and project.customer_id else None
                ),
                "days_in_stage": days,
                "stale": o.status == "sent" and days >= STALE_AFTER_DAYS,
            }
        )
    for rows in columns.values():
        rows.sort(key=lambda r: -r["days_in_stage"])
    return {"stale_after_days": STALE_AFTER_DAYS, "columns": columns}
