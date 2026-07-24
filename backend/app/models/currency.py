"""Tenant currency catalog (Slice 5a of the data model).

A per-tenant registry of the currencies a workspace transacts in — the source of
the invoice/issuing currency pickers and of display metadata (symbol, minor
units). Amounts are still stored as `Numeric(14,2)`; `decimal_places` is display
metadata (JPY=0, most=2, a few=3), not a storage change.

Tenant-scoped so each org curates its own active set; mirrors the tax-code
catalog (unique code per org, `UNIQUE(org_id, id)` as a future FK target, an
active flag — archived never hard-deleted — and an optimistic-concurrency
`version`).
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class Currency(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "currencies"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_currencies_org_code"),
        UniqueConstraint("org_id", "id", name="uq_currencies_org_id"),
        Index("ix_currencies_org_active", "org_id", "active"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 4217
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(8), nullable=True)
    decimal_places: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
