"""Supplier payment runs (Phase 14).

The AP counterpart of `ReimbursementBatch`: a payout run groups scheduled-for-
payment supplier invoices for payment together, under a shared method + reference,
and is exportable as a bank file. Marking a run paid writes an AP payment-ledger
entry for each linked invoice (settling it in full) and advances its workflow to
`paid`. A paid run is a reconciliation debit target (one bank transfer). The
invoice→run link is a soft `invoices.payment_run_id` (mirroring the expense
report→batch link). Tenant-scoped.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
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

RUN_OPEN = "open"
RUN_APPROVED = "approved"  # a second person reviewed the run (WO-9 maker≠checker)
RUN_PAID = "paid"
RUN_CANCELLED = "cancelled"
RUN_STATUSES = (RUN_OPEN, RUN_APPROVED, RUN_PAID, RUN_CANCELLED)


class PaymentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payment_runs"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_payment_runs_org_id"),
        Index("ix_payment_runs_org_status", "org_id", "status"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    method: Mapped[str] = mapped_column(String(40), default="bank_transfer", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=RUN_OPEN, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Sum of the linked invoices' EUR totals (invoices may be multi-currency).
    total_eur: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # WO-9 segregation of duties: the maker (creator) and checker (approver) are
    # recorded by IMMUTABLE user id (the email columns are display copies — an
    # account can change its email, an id it cannot). Enforcement compares both.
    created_by_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_by_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # WO-9 export-once: when the FIRST bank file left the building, how many were
    # produced in total, and the last pain.001 MsgId sent — so a bank query about
    # a message id can be answered later (every export is also audited).
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    export_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_msg_id: Mapped[str | None] = mapped_column(String(35), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
