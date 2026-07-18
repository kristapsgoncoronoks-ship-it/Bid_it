from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrgModule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-org activation state for a platform module. A missing row means the
    module is at its registry default; core modules are always on."""

    __tablename__ = "org_modules"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_org_module"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
