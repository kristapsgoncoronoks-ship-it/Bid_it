from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.invoice import Invoice
    from app.models.organization import Organization

# Vendor trust states (WO-2): `provisional` marks a vendor whose bank/tax
# identity arrived with its creation (captured, not yet independently verified)
# — a payment run refuses it unless explicitly confirmed.
VENDOR_ACTIVE = "active"
VENDOR_PROVISIONAL = "provisional"
VENDOR_STATUSES = (VENDOR_ACTIVE, VENDOR_PROVISIONAL)


class Vendor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vendors"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_vendor_org_name"),
        # Composite-FK target so child rows (vendor_change_requests) are
        # cross-tenant-safe by construction: (org_id, vendor_id) must match.
        UniqueConstraint("org_id", "id", name="uq_vendors_org_id"),
        # Country breakdowns in the explore pivot (tenant-scoped).
        Index("ix_vendors_org_country", "org_id", "country"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Bank details for AP payment files (Phase 17): the creditor account a SEPA
    # pain.001 credit transfer pays into. Protected fields (WO-2): a CHANGE to a
    # stored iban/tax_id goes through `vendor_change_requests`, never a direct
    # write — see services/vendors.py.
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    bic: Mapped[str | None] = mapped_column(String(11), nullable=True)
    # Optimistic concurrency (mirrors Invoice.version): the client sends the
    # version it read; a stale write is 409.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=VENDOR_ACTIVE, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="vendors")
    invoices: Mapped[list[Invoice]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )
