"""Accounts-receivable payment ledger (Slice 3c of the data model).

Extracts the inline `issued_invoices.amount_paid` running total into a proper
history of settlement entries. The ledger is the SOURCE OF TRUTH for payment
history; `issued_invoices.amount_paid` becomes a derived cache
(= SUM(payments.amount)) that every existing read path keeps using unchanged.

`amount` is SIGNED — a positive receipt, or a negative correction/refund — so a
downward adjustment of the cumulative total stays auditable rather than silently
lowering a number. A cross-tenant reference is structurally impossible: the
composite FK `(org_id, issued_invoice_id) → issued_invoices(org_id, id)` (the
same guard the cost-allocation links use).

Splitting one receipt across several invoices is modelled WITHOUT a separate
`payment_allocations` table: a receipt allocation is just a `payments` row whose
`receipt_id` is set (see `app.models.receipt` / `app.services.receipts`), and
reversing an allocation appends an offsetting negative row — so the ledger stays
the single append-only source of truth for `amount_paid`.
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
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

Money = Numeric(14, 2)


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "issued_invoice_id"],
            ["issued_invoices.org_id", "issued_invoices.id"],
            name="fk_payments_issued_invoice",
            ondelete="CASCADE",
        ),
        # Slice 5c: the receipt this entry was allocated from (a bank transfer may
        # settle several invoices). Composite FK → tenant-safe. CASCADE is nominal
        # (receipts aren't hard-deleted outside an org purge, where both cascade).
        ForeignKeyConstraint(
            ["org_id", "receipt_id"],
            ["receipts.org_id", "receipts.id"],
            name="fk_payments_receipt",
            ondelete="CASCADE",
        ),
        Index("ix_payments_org_invoice", "org_id", "issued_invoice_id"),
        Index("ix_payments_org_receipt", "org_id", "receipt_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    issued_invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    # Signed: positive receipt, negative correction/refund. SUM = amount_paid.
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(20), default="bank_transfer", nullable=False)
    reference: Mapped[str | None] = mapped_column(String(140), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when this entry is a receipt allocation (Slice 5c); NULL for a direct
    # cumulative payment recorded via PATCH /payment.
    receipt_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
