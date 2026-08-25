"""CRM light (WO-H, docs/design/crm-module-research.md Part 2).

Two deliberately small tables — the research's whole point is that the
segment norm is "what InvoiceIQ already has, plus notes and a derived
timeline", NOT a CRM platform:

- `customer_notes` — free-text notes on the customer master. The timeline
  itself is DERIVED (a read over existing audited events + these notes);
  the note is the only hand-written part.
- `offer_stage_events` — one row per offer status transition, stamped at
  write time. Cheap now, impossible to reconstruct later (the research's
  words): time-in-stage and staleness flags read from here.

The lifecycle stage lives as a COLUMN on `customers` (prospect | active |
dormant | lost) — no lead entity, ever: the hard lead→customer conversion
step is the documented duplicate factory this design explicitly rejects.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

CUSTOMER_LIFECYCLES = ("prospect", "active", "dormant", "lost")
LIFECYCLE_CHECK = "lifecycle IN ('prospect', 'active', 'dormant', 'lost')"


class CustomerNote(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One free-text note on a customer, newest first on the timeline."""

    __tablename__ = "customer_notes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "customer_id"],
            ["customers.org_id", "customers.id"],
            name="fk_customer_notes_customer",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_customer_notes_org_id"),
        Index("ix_customer_notes_org_customer", "org_id", "customer_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class OfferStageEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One offer status transition (from_status NULL = the offer was created).

    `created_at` from TimestampMixin IS the transition moment; days-in-stage
    for the kanban's staleness flag = now − the offer's latest event.
    """

    __tablename__ = "offer_stage_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "offer_id"],
            ["project_offers.org_id", "project_offers.id"],
            name="fk_offer_stage_events_offer",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_offer_stage_events_org_id"),
        Index("ix_offer_stage_events_org_offer", "org_id", "offer_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    offer_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(320), nullable=True)
