from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class EmailMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An OUTBOUND email the platform produced — the sent-mail record / outbox.

    Generic mail/reminders retain the original recorded/sent/failed contract.
    R011 invoice delivery persists command identity, sender and an object-storage
    PDF reference, then queues a durable worker job. Its states are blocked,
    queued, sending, sent, failed or unknown; sent means SMTP acceptance only.
    Historical rows are not reclassified. No credentials or document bytes live
    in this table.
    """

    __tablename__ = "email_messages"
    __table_args__ = (
        Index("ix_email_org_created", "org_id", "created_at"),
        UniqueConstraint("org_id", "command_key", name="uq_email_command"),
        Index(
            "uq_email_primary_delivery",
            "org_id",
            "invoice_id",
            unique=True,
            postgresql_where=text("is_primary = true"),
            sqlite_where=text("is_primary = 1"),
        ),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which issued invoice this concerns (nullable for future non-invoice mail).
    invoice_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    is_primary: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    command_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    command_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attachment_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attachment_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # invoice | reminder
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(12), default="recorded", nullable=False
    )  # legacy recorded; invoice delivery blocked/queued/sending/sent/failed/unknown
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
