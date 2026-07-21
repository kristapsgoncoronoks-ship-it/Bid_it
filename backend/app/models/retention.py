from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class RetentionPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A per-tenant data-retention rule: keep `category` records for `retain_days`
    since creation, then a scheduled purge deletes the rest (GDPR Art. 5(1)(e)
    storage limitation). The ABSENCE of a row means keep-forever — so retention
    is strictly opt-in per category and safe by default. `(org_id, category)` is
    unique; setting `retain_days <= 0` removes the policy (disables purging)."""

    __tablename__ = "retention_policies"
    __table_args__ = (
        UniqueConstraint("org_id", "category", name="uq_retention_org_category"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)   # invoices | expenses | email_intake
    retain_days: Mapped[int] = mapped_column(Integer, nullable=False)


class LegalHold(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A litigation / audit hold that SUSPENDS all retention purges for a tenant
    while active (e-discovery preservation duty overrides data minimization). A
    hold is placed with a reason, then explicitly released — never deleted, so
    the preservation record survives. Any active hold blocks every purge."""

    __tablename__ = "legal_holds"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    placed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
