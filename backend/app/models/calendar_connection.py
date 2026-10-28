"""Personal calendar exports and their durable, tenant-bound command evidence.

Event mappings retain the source assignment UUID after deletion so reconciliation
can remove a managed external event without rediscovering the private document.
Credentials are sealed; public API responses never serialize these models.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class CalendarConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "calendar_connections"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "provider", name="uq_calendar_connection_owner"),
        UniqueConstraint("org_id", "id", name="uq_calendar_connections_org_id_id"),
        Index("ix_calendar_connections_org_user", "org_id", "user_id"),
    )

    org_id: Mapped[str] = mapped_column(GUID(), ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(12))
    account_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="connecting")
    sealed_tokens: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    calendar_name: Mapped[str] = mapped_column(String(120), default="Bid_it work schedule")
    calendar_marker: Mapped[str] = mapped_column(String(100), default="")
    setup_started: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(120), nullable=True)


class CalendarOAuthAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "calendar_oauth_attempts"
    __table_args__ = (
        Index("ix_calendar_oauth_attempts_org_expiry", "org_id", "expires_at"),
        ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["calendar_connections.org_id", "calendar_connections.id"],
            ondelete="CASCADE",
            name="fk_calendar_oauth_connection",
        ),
    )

    org_id: Mapped[str] = mapped_column(GUID(), ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(GUID(), ForeignKey("sessions.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(12))
    connection_id: Mapped[str] = mapped_column(GUID())
    connection_generation: Mapped[int] = mapped_column(Integer)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    sealed_verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CalendarEventMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "calendar_event_mappings"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "connection_id", "assignment_id", name="uq_calendar_event_source"
        ),
        ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["calendar_connections.org_id", "calendar_connections.id"],
            ondelete="CASCADE",
            name="fk_calendar_events_connection",
        ),
        Index("ix_calendar_events_org_connection", "org_id", "connection_id"),
    )

    org_id: Mapped[str] = mapped_column(GUID(), ForeignKey("organizations.id", ondelete="CASCADE"))
    connection_id: Mapped[str] = mapped_column(GUID())
    # Deliberate tombstone, not a cascading assignment FK: preserve cleanup identity.
    assignment_id: Mapped[str] = mapped_column(GUID())
    stable_id: Mapped[str] = mapped_column(String(36))
    event_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    desired_revision: Mapped[int] = mapped_column(Integer, default=1)
    synced_revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    error: Mapped[str | None] = mapped_column(String(120), nullable=True)
    create_started: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
