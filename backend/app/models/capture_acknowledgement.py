"""Acknowledgements of failed captures (H-1, the failed-capture worklist).

One row per acknowledgement — not one row per failure. Acknowledging is a RECORD
(who, when, an optional note), so the table is APPEND-ONLY and keeps the history;
the "current" acknowledgement for a capture is the newest row, decided at read
time in `services/capture_failures.py`.

`failure_seen_at` is the timestamp of the failure the acknowledgement was made
AGAINST. It is what makes an ack survivable: a capture that is retried and fails
again returns to the worklist instead of inheriting the earlier dismissal.

The reference is a soft polymorphic one (`channel` + `ref_id`) rather than an FK,
because the two capture channels live in different tables (`extraction_runs` for
a direct upload, `inbound_invoices` for an emailed attachment) and an operator's
worklist spans both. Tenant-scoped: registered in `app/core/tenant.py::
TENANT_MODELS` and RLS-policy'd in the same migration that creates it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class CaptureAcknowledgement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capture_acknowledgements"
    __table_args__ = (Index("ix_capture_acks_org_ref", "org_id", "channel", "ref_id"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # upload | email — see capture_failures.CHANNELS.
    channel: Mapped[str] = mapped_column(String(12), nullable=False)
    # extraction_runs.id or inbound_invoices.id, per `channel`.
    ref_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The failure this acknowledgement covers. A later failure is not covered.
    # F-06: the failure SEQUENCE this acknowledgement covers. Coverage is
    # `ack.failure_seq >= record.failure_seq` — integers, so a re-failure can
    # never collide with the acknowledgement that preceded it.
    failure_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # Kept as information ("acknowledged against the failure of 14 Aug"), no
    # longer the basis of the coverage decision.
    failure_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
