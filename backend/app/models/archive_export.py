"""WO-AI — the ex-client archive export request (owner decision 2026-08-16 §1.C).

The archive SURVIVES a client who leaves, for the full retention period, and
"an ex-client can request a one-time EXPORT of their archive; no live login is
retained". This row is that request: who asked (the last recorded owner's
address), what the worker produced (one zip in the `exports` document class),
and whether the one-time link has been used. The link itself is an
`EmailToken` (purpose `archive_export`) — the same single-use, hashed,
expiring credential a password reset uses — so nothing here stores a secret.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

EXPORT_STATUSES = ("queued", "ready", "failed", "downloaded")
_STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in EXPORT_STATUSES) + ")"


class ArchiveExport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "archive_exports"
    __table_args__ = (CheckConstraint(_STATUS_CHECK, name="ck_archive_exports_status"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The address the one-time link goes to — an owner's, live or last recorded.
    requested_email: Mapped[str] = mapped_column(String(320), nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")
    job_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    # What the worker produced (document class `exports`).
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    records: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missing_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
