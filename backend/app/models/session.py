"""Server-side sessions for token revocation (Slice 4).

A JWT is stateless, so on its own it cannot be revoked before it expires. Each
login creates a `sessions` row whose id is embedded in the token as the `jti`
claim; every authenticated request verifies the session still exists and is not
revoked or expired. Logout, "sign out everywhere", a password reset, and account
deactivation all revoke sessions — killing the token immediately.

Tenant-stamped (org_id + RLS + ORM guard). Validation runs pre-scope (by jti), so
the tenant guard is inert on that lookup.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class Session(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_org_user", "org_id", "user_id"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Best-effort client fingerprint for the "your sessions" list.
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
