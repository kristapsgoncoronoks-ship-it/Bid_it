from __future__ import annotations

from sqlalchemy import ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class IssuerProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The org's own company details used as the SELLER on issued invoices.

    Carries everything EN 16931 / VAT Directive 2006/112/EC Art. 226 requires of
    the supplier, plus payment details and an optional logo for the PDF. One per
    organization.
    """

    __tablename__ = "issuer_profiles"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trade_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vat_number: Mapped[str | None] = mapped_column(String(32), nullable=True)   # e.g. DE123456789
    registration_number: Mapped[str | None] = mapped_column(String(64), nullable=True)  # company/trade register

    address_line1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)       # ISO 3166-1 alpha-2

    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    bic: Mapped[str | None] = mapped_column(String(11), nullable=True)

    default_currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    invoice_prefix: Mapped[str] = mapped_column(String(16), default="INV-", nullable=False)
    next_number: Mapped[int] = mapped_column(default=1, nullable=False)
    payment_terms_days: Mapped[int] = mapped_column(default=14, nullable=False)

    logo_mime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # footer / legal notes
