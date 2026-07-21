from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ProcessedStripeEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Idempotency ledger for inbound Stripe webhook events (ADR-0011).

    Stripe guarantees *at-least-once* delivery and will redeliver on any non-2xx
    or timeout, so the same `event_id` can arrive twice. We record each processed
    id under a UNIQUE constraint and skip a duplicate before applying any effect.

    Platform-level (NOT tenant-scoped): the webhook has no authenticated org and
    resolves the tenant from the Stripe customer id. Deliberately keyed by the
    Stripe event id alone, not org_id, so it is never touched by the tenant guard.
    """

    __tablename__ = "processed_stripe_events"

    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
