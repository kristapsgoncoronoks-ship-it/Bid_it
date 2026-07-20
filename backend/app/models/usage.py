from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class UsageCounter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A per-tenant, per-month metered counter (e.g. document uploads).

    Invoices are counted directly off the invoices table; metrics without their
    own fact table (uploads) accumulate here. One row per (org, period, metric).
    """

    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint("org_id", "period", "metric", name="uq_usage_org_period_metric"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)   # YYYY-MM
    metric: Mapped[str] = mapped_column(String(40), nullable=False)  # e.g. "upload"
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
