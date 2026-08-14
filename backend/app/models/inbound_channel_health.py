"""Per-channel inbound health (H-2).

One row per (org, channel): did the most recent delivery attempt on this inbound
channel succeed, when did one last succeed, and how many have failed in a row.

WHY IT IS NOT DERIVED FROM DOCUMENT COUNTS
------------------------------------------
The failure this table exists to catch is a channel that dies while every
dashboard stays green — because "no documents arrived" and "nothing could get
through" produce identical silence. Counting documents can never separate them.
Only an explicitly recorded per-attempt outcome can.

WHY `expected_cadence_days` IS NULLABLE AND NOTHING DEFAULTS IT
--------------------------------------------------------------
Staleness is only meaningful against an expected rhythm. A customer who receives
one invoice a month is not broken at 14 days of quiet; a customer whose supplier
emails daily is broken at three. We do not know which without being told, so the
column is NULL until someone sets it, and a NULL means the health view reports
the elapsed time as a FACT and declines to call the channel broken. Guessing a
threshold would generate false alarms, and an alarm nobody trusts is worse than
no alarm.

Tenant-scoped: registered in `app/core/tenant.py::TENANT_MODELS` and RLS-policy'd
in the same migration that creates it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class InboundChannelHealth(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "inbound_channel_health"
    __table_args__ = (
        UniqueConstraint("org_id", "channel", name="uq_inbound_channel_health_org_channel"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The inbound channel key — `email` today. See inbound_health.CHANNELS.
    channel: Mapped[str] = mapped_column(String(24), nullable=False)

    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Reset to 0 by a success. Incremented at the START of every attempt, so an
    # attempt that crashes without reporting anything still counts as a failure.
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # A classified kind from `inbound_health.ERROR_KINDS`, never free-form prose.
    last_error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # How often this org EXPECTS deliveries. NULL = not stated; the health view
    # then reports elapsed time without claiming the channel is broken.
    expected_cadence_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
