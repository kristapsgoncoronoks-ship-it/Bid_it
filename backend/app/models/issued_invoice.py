from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

Money = Numeric(14, 2)


class IssuedInvoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An invoice this org ISSUES to a customer (outbound / accounts-receivable).

    Distinct from the received invoices used for spend analytics. A snapshot of
    the seller is taken at issue time so a later profile edit never rewrites a
    finalized invoice. Numbering is sequential per issuer.
    """

    __tablename__ = "issued_invoices"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    number: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    supply_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)

    # Buyer (Art. 226)
    buyer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    buyer_vat_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    buyer_address_line1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    buyer_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    buyer_postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    buyer_country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # Seller snapshot (frozen at issue)
    seller_json: Mapped[str] = mapped_column(Text, nullable=False)

    # VAT treatment: 'standard' | 'reverse_charge' | 'intra_eu' | 'exempt'
    vat_scheme: Mapped[str] = mapped_column(String(20), default="standard", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    subtotal: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)

    lines: Mapped[list["IssuedInvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="IssuedInvoiceLine.position"
    )


class IssuedInvoiceLine(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "issued_invoice_lines"

    invoice_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("issued_invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(default=1, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"), nullable=False)
    unit: Mapped[str] = mapped_column(String(8), default="C62", nullable=False)  # UN/ECE unit code
    unit_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)

    invoice: Mapped["IssuedInvoice"] = relationship(back_populates="lines")
