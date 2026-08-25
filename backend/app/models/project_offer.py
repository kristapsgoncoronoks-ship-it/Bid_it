"""Offers/estimates and the invoicing plan — phases 4 of the project lifecycle.

`docs/design/project-profitability.md` §5a, on the owner's decisions of
2026-08-16: an OFFER is the project's first artifact, issued before any
contract; it is VERSIONABLE (offers get revised); its numbering scheme is SET
BY THE CLIENT (per-org prefix — the platform enforces exactly one thing,
per-org uniqueness); and an ACCEPTED offer seeds the INVOICING PLAN — the
contracted schedule tracked against what was actually issued, so
"contracted 3 × 10,000, issued 2" is a live receivable instead of a memory.

Industry-neutral (owner requirement): nothing here names an industry.

Lines are stored WHOLE as JSON (`line_items_json`), the archive's pattern: an
offer is a document, read back or not at all — nothing queries into its lines,
and a second line table would mean a second parity probe and RLS policy for no
query anyone runs.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

Money = Numeric(14, 2)

OFFER_STATUSES = ("draft", "sent", "accepted", "rejected", "superseded")


class ProjectOffer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One version of one offer. A revision is a NEW row with the same number
    and version+1; the prior version is marked superseded — the history of what
    was offered is part of the record, not something an edit may overwrite."""

    __tablename__ = "project_offers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_project_offers_project",
            ondelete="CASCADE",
        ),
        # The platform's ONE numbering rule, whatever scheme the client picks:
        # a number+version is unambiguous within the org.
        UniqueConstraint("org_id", "number", "version", name="uq_project_offers_number_version"),
        UniqueConstraint("org_id", "id", name="uq_project_offers_org_id"),
        Index("ix_project_offers_org_project", "org_id", "project_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    number: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    line_items_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # WO-I client portal: the quote-viewed signal — stamped the FIRST time the
    # customer's portal renders this offer, surfaced on the CRM timeline.
    # A stamp, not a stage event: viewing is information, not movement, so it
    # must never reset the pipeline's days-in-stage.
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvoicingPlanRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One agreed instalment of the contracted sum (advance, stage, interim…).

    The plan is what the contract PROMISES; the P&L's revenue is what was
    actually issued. The tracking view compares the two — the gap is a live
    receivable the client can see instead of remember."""

    __tablename__ = "invoicing_plan_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_invoicing_plan_rows_project",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_invoicing_plan_rows_org_id"),
        Index("ix_invoicing_plan_rows_org_project", "org_id", "project_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
