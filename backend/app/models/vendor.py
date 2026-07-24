from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.invoice import Invoice
    from app.models.organization import Organization


class Vendor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vendors"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_vendor_org_name"),
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
    # pain.001 credit transfer pays into.
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    bic: Mapped[str | None] = mapped_column(String(11), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="vendors")
    invoices: Mapped[list[Invoice]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )
