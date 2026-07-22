from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import UserRole


class Invitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A pending invite for someone to join a tenant. Accepting it creates a
    user in that org. Token-based (emailed to the invitee; the owner can also
    share the link). Expires after a fixed window."""

    __tablename__ = "invitations"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=False, length=20),
        default=UserRole.user,
        nullable=False,
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    invited_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # NULL = never expires (pre-existing invites); new invites are stamped +14d.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
