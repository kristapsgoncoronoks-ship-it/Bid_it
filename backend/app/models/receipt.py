"""Incoming-payment receipts (Slice 5c of the data model).

A `receipt` is money actually RECEIVED — typically one bank transfer — that may
settle several issued invoices. Allocating a receipt to an invoice creates a
`payments` ledger entry (Slice 3c) stamped with this receipt's id, so the
invoice's `amount_paid` cache stays the single source of truth and a receipt's
allocations are exactly its `payments` rows. `amount − SUM(allocated)` is the
still-unallocated balance.

Tenant-scoped (org_id + RLS + ORM guard). `UNIQUE(org_id, id)` is the
composite-FK target for `payments.receipt_id`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

Money = Numeric(14, 2)


class Receipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "receipts"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_receipts_org_id"),
        Index("ix_receipts_org_received", "org_id", "received_on"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)  # total received
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(20), default="bank_transfer", nullable=False)
    reference: Mapped[str | None] = mapped_column(String(140), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
