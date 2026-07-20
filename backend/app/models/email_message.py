from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class EmailMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An OUTBOUND email the platform produced — the sent-mail record / outbox.

    Every send (an issued invoice, a payment reminder) is recorded here first, so
    there is an auditable history regardless of whether an SMTP relay is wired up.
    When SMTP is configured the message is also delivered and `status` becomes
    'sent'; otherwise it stays 'recorded' (demo/no-relay) — delivery never fails a
    request. No secrets or attachments are stored, only the rendered text body.
    """

    __tablename__ = "email_messages"
    __table_args__ = (Index("ix_email_org_created", "org_id", "created_at"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which issued invoice this concerns (nullable for future non-invoice mail).
    invoice_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)      # invoice | reminder
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(12), default="recorded", nullable=False)  # recorded|sent|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
