"""Organization membership (Slice 6a — multi-org foundation).

The tenant relationship between a global identity (`User`) and an `Organization`.
Splitting membership out of the user row is what lets one person belong to — and
switch between — several organizations. See
docs/security/multi-org-membership-plan.md.

This slice only ADDS the table and backfills one membership per existing user;
nothing reads it yet (`user.org_id`/`user.role` stay authoritative until the
contract step). Tenant-scoped (org_id + RLS + ORM guard).
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import UserRole


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        # One membership per (user, org).
        UniqueConstraint("user_id", "org_id", name="uq_memberships_user_org"),
        Index("ix_memberships_org_user", "org_id", "user_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=False, length=20),
        default=UserRole.user,
        nullable=False,
    )
    is_expense_approver: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )  # active|suspended
    # Denormalised identity snapshot (Slice 6e) so the roster reads ONLY
    # memberships — org-scoped + RLS-safe, and complete (includes members who are
    # currently active in another org). Kept in sync from the User on write.
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
