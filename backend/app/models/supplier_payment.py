"""Accounts-payable payment ledger (Phase 13 of the data model).

The AP mirror of the AR `payments` ledger: a history of settlement entries behind
a received supplier `invoices.amount_paid` cache. The ledger is the SOURCE OF
TRUTH for what has been paid against a supplier invoice; `invoices.amount_paid`
becomes a derived running-total cache (= SUM(supplier_payments.amount)) and the
paid/outstanding/overdue status is derived on read (services/ap_status.py).

`amount` is SIGNED — a positive payment out, a negative correction/refund — so a
downward adjustment of the cumulative total stays auditable rather than silently
lowering a number. A cross-tenant reference is structurally impossible: the
composite FK `(org_id, invoice_id) → invoices(org_id, id)`. `run_id` is the future
payment-run this entry was paid in (Phase 13b) — nullable for a direct payment.
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


class SupplierPayment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "supplier_payments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_supplier_payments_invoice",
            ondelete="CASCADE",
        ),
        Index("ix_supplier_payments_org_invoice", "org_id", "invoice_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    # Signed: positive payment out, negative correction/refund. SUM = amount_paid.
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(20), default="bank_transfer", nullable=False)
    reference: Mapped[str | None] = mapped_column(String(140), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The payment run this entry was paid in (Phase 13b); NULL for a direct payment.
    run_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
