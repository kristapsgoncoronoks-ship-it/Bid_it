"""The client portal's magic link (WO-I, crm-module-research Part 3).

One row per customer per issue: the token IS the credential (the dominant
model in this segment — clients who visit twice a year never manage a
password). Revocation is a stamp, not a delete: `revoked_at` keeps the
issue/revoke history readable, and resolving ignores revoked rows, so an
old link dies the moment a new one is issued. No expiry column in v1 —
revocable-on-demand is the property that matters for a standing portal
link, and regenerate is one click.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class CustomerPortalToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A customer's portal capability. Live = revoked_at IS NULL."""

    __tablename__ = "customer_portal_tokens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "customer_id"],
            ["customers.org_id", "customers.id"],
            name="fk_customer_portal_tokens_customer",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_customer_portal_tokens_org_id"),
        UniqueConstraint("token", name="uq_customer_portal_tokens_token"),
        Index("ix_customer_portal_tokens_org_customer", "org_id", "customer_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
