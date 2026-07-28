from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RolePolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The **system matrix** — per-role usage limits, system-wide (not tenant
    scoped). Sysadmin-editable. A limit of 0 means unlimited.

    Kept deliberately small and typed: the two numeric limits that gate the free
    vs paid tiers today. New limits append as columns as the product grows.
    """

    __tablename__ = "role_policies"
    __table_args__ = (UniqueConstraint("role", name="uq_role_policy_role"),)

    role: Mapped[str] = mapped_column(String(20), nullable=False)
    monthly_invoice_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    monthly_upload_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
