"""Supplier agreed prices (WO-G phase 2, docs/design/supplier-cost-analytics.md).

The cost-control half of the supplier analytics: the tenant records what a
price SHOULD be (per supplier × item, with a validity window), and the
system says so wherever the invoiced price disagrees — an advisory
validation finding at capture, an org-opt-in block at the submit gate, and
the overcharge worklist on the analytics surface.

One deliberate identity choice: the item is the same normalised string
phase 1's read models group by (`lower(trim(description))`) — no separate
item master. An agreed price therefore matches exactly the lines the
history graph shows, and nothing else.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

Money = Numeric(14, 2)


class SupplierAgreedPrice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One agreed unit price for a supplier × item, valid over a date window.

    `valid_to` NULL = open-ended. Overlapping windows are allowed (a
    renegotiation mid-window); resolution picks the row with the LATEST
    `valid_from` that covers the date — the most recent agreement wins.
    """

    __tablename__ = "supplier_agreed_prices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "vendor_id"],
            ["vendors.org_id", "vendors.id"],
            name="fk_supplier_agreed_prices_vendor",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_supplier_agreed_prices_org_id"),
        # One list entry per supplier × item × currency × window start: editing
        # a price for the same start date is an update, never a duplicate row.
        UniqueConstraint(
            "org_id",
            "vendor_id",
            "item",
            "currency",
            "valid_from",
            name="uq_supplier_agreed_prices_entry",
        ),
        Index("ix_supplier_agreed_prices_org_vendor", "org_id", "vendor_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    #: normalised item key — always lower(trim(...)), the phase-1 identity.
    item: Mapped[str] = mapped_column(String(500), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    agreed_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
