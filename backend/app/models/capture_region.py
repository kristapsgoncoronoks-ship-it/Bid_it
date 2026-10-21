"""Immutable selected-region reads and their explicit, replay-safe application."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class CaptureRegionRead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capture_region_reads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "extraction_run_id"],
            ["extraction_runs.org_id", "extraction_runs.id"],
            ondelete="CASCADE",
            name="fk_capture_region_run",
        ),
        UniqueConstraint("org_id", "requested_by", "command_key", name="uq_capture_region_command"),
        Index("ix_capture_region_org_run", "org_id", "extraction_run_id", "created_at"),
        Index("ix_capture_region_org_status", "org_id", "status"),
    )

    org_id: Mapped[str] = mapped_column(GUID(), ForeignKey("organizations.id", ondelete="CASCADE"))
    extraction_run_id: Mapped[str] = mapped_column(GUID())
    requested_by: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    command_key: Mapped[str] = mapped_column(String(36))
    payload_hash: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    attempt_seq: Mapped[int] = mapped_column(Integer)
    request_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    processing_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    processing_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    apply_command_key: Mapped[str | None] = mapped_column(String(36), nullable=True)
    apply_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    apply_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_draft_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_by: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
