"""Single-use email tokens for email verification and password reset (Slice 3).

Only a SHA-256 HASH of the token is stored — the raw token exists solely in the
emailed link, so a database leak yields no usable tokens. A token is valid until
`expires_at`, is consumed exactly once (`used_at`), and is bound to a purpose so a
verification token can never be replayed as a password reset.

Tenant-stamped (org_id + RLS + ORM guard) for auditability; lookups are by the
globally-unique `token_hash` and run pre-auth (unscoped), so the guard is inert
on those reads.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

PURPOSE_VERIFY_EMAIL = "verify_email"
PURPOSE_PASSWORD_RESET = "password_reset"


class EmailToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "auth_tokens"
    __table_args__ = (Index("ix_auth_tokens_org_user", "org_id", "user_id"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # verify_email|password_reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
