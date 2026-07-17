from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.vendor import Vendor


class InvoiceStatus(str, enum.Enum):
    draft = "draft"
    pending = "pending"
    paid = "paid"
    overdue = "overdue"


# 14 digits total, 2 after the decimal — exact money, never float.
Money = Numeric(14, 2)


class Invoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoices"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )

    invoice_number: Mapped[str] = mapped_column(String(120), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        SAEnum(InvoiceStatus, name="invoice_status"),
        default=InvoiceStatus.pending,
        nullable=False,
        index=True,
    )

    subtotal: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)

    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    vendor: Mapped["Vendor"] = relationship(back_populates="invoices")
    line_items: Mapped[list["LineItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="LineItem.created_at",
    )


class LineItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "line_items"

    invoice_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(80), default="uncategorized", nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)

    invoice: Mapped["Invoice"] = relationship(back_populates="line_items")
