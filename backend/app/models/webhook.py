from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# Delivery lifecycle.
PENDING = "pending"
DELIVERED = "delivered"
FAILED = "failed"


class WebhookEndpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant-registered HTTP endpoint that receives event notifications.

    Each event is POSTed as JSON, signed with the endpoint's secret (HMAC-SHA256)
    so the receiver can verify authenticity. `events` is a comma-separated list of
    event types, or "*" for all.
    """

    __tablename__ = "webhook_endpoints"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret: Mapped[str] = mapped_column(String(80), nullable=False)   # signing key
    events: Mapped[str] = mapped_column(String(500), default="*", nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)


class WebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One attempt-tracked delivery of an event to one endpoint (the audit log).

    Actual delivery + retry is driven by the durable job queue; this row records
    the outcome the queue produced so the UI can show what happened.
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliv_endpoint", "endpoint_id", "created_at"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(12), default=PENDING, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
