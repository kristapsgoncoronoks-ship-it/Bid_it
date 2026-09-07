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
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    metric: Mapped[str] = mapped_column(String(40), nullable=False)  # e.g. "upload"
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Cumulative quantity the billing provider has ACKNOWLEDGED (Stripe
    # metered/overage, ADR-0013).
    reported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # BILL-METER-001 (Lago reference integration 2026-09-07): the cumulative
    # boundary of the segment currently being reported. COMMITTED before the
    # provider call, so a timeout / lost response retries the SAME quantity and
    # identifier even if `count` grew in the meantime. Invariant:
    # reported <= reporting_target <= count (repaired defensively if violated).
    reporting_target: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
