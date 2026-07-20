from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class EmailIntake(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-org inbound-email configuration: a unique address token invoices can
    be emailed to. One row per org; the address is ``<token>@<inbound-domain>``."""

    __tablename__ = "email_intakes"
    __table_args__ = (UniqueConstraint("org_id", name="uq_email_intake_org"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Globally unique so an inbound address maps to exactly one tenant.
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)


class InboundInvoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An invoice received by email — one row per attachment. Parsed through the
    same extraction engine into a draft a user confirms into an Invoice."""

    __tablename__ = "inbound_invoices"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_addr: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # pending → confirmed | failed | discarded
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    method: Mapped[str | None] = mapped_column(String(20), nullable=True)   # detected type / parse path
    draft_json: Mapped[str | None] = mapped_column(Text, nullable=True)     # ParsedInvoiceDraft JSON
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    file_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # original attachment

    invoice_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
