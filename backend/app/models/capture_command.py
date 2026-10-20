"""Committed capture admission identity; retained when its capture is purged."""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class CaptureCommand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capture_commands"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_capture_commands_org_id"),
        UniqueConstraint("org_id", "command_key", name="uq_capture_commands_key"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    command_key: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
