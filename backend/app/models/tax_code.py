"""Tenant tax-code catalog (Slice 4a of the data model).

A per-tenant registry of VAT/tax codes — a named rate + category (+ optional
country) an invoice line can reference instead of a free-typed percentage. Today
issued-invoice lines carry a raw `vat_rate`; this catalog is the normalised
source those rates come from (the issuing UI's rate picker; a later slice adds a
`tax_code_id` link on the lines — the same dual-read pattern the cost-allocation
dimensions used).

Design principles (see docs/architecture/data-model.md), mirroring the
cost-allocation master: tenant boundary (`org_id` + RLS + ORM guard), unique code
per org, `UNIQUE(org_id, id)` as a future composite-FK target, an active flag
(archived, never hard-deleted so historical references still resolve), and an
optimistic-concurrency `version`.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# Rate is a percentage with 3 decimals (covers every real VAT rate, incl. 8.1%).
Rate = Numeric(6, 3)

CATEGORIES = ("standard", "reduced", "zero", "exempt", "reverse_charge")


class TaxCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tax_codes"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_tax_codes_org_code"),
        # Future composite-FK target from issued_invoice_lines (tenant-safe refs).
        UniqueConstraint("org_id", "id", name="uq_tax_codes_org_id"),
        Index("ix_tax_codes_org_active", "org_id", "active"),
        Index("ix_tax_codes_org_country", "org_id", "country"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False)  # percent
    category: Mapped[str] = mapped_column(String(20), default="standard", nullable=False)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)  # ISO; null = generic
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
