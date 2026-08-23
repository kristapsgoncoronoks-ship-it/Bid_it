"""Personal calendar-feed tokens (WO-B, docs/design/work-calendar.md B2).

One row per (org, user): a secret capability that serves THAT user's
assignments as an iCalendar feed to whatever calendar app polls it
(Google/Apple/Microsoft all subscribe to ICS URLs). The token IS the
credential — the feed route is public and unauthenticated, resolved the
same way the email-intake webhook resolves its recipient token (unscoped
lookup, then explicit tenant scope). Regenerating replaces the token value,
killing the old URL immediately.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class CalendarFeedToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's revocable calendar-feed capability."""

    __tablename__ = "calendar_feed_tokens"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_calendar_feed_tokens_org_user"),
        UniqueConstraint("token", name="uq_calendar_feed_tokens_token"),
        Index("ix_calendar_feed_tokens_org_id", "org_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
