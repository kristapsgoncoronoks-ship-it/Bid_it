from __future__ import annotations

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

Money = Numeric(14, 2)


class BudgetTarget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A recurring monthly spending limit for one category (household/monthly
    budgeting over received invoices). One row per (org, category), amount in EUR."""

    __tablename__ = "budget_targets"
    __table_args__ = (UniqueConstraint("org_id", "category", name="uq_budget_org_category"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    monthly_limit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
