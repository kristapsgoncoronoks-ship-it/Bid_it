"""PROD-009 — the whole-workspace data export request.

The archive export (WO-AI, `archive_exports`) answers a narrow question: an
ex-client asking for the invoices the platform kept ON their behalf after they
left. It reads ONE table. This row is the other question, the one the audit
found unanswered — a LIVE customer asking for everything the platform holds
about their workspace, so that leaving is a decision they can make rather than
a threat they have to accept.

Deliberately a separate table from `archive_exports`, not a `scope` column on
it. `archive_exports` has a PUBLIC, unauthenticated door (an ex-client whose
login is gone asks by email, and the response never says whether the address
was an owner's). A whole-workspace dump of a LIVE tenant must never be
reachable from that door, and the cheapest way to guarantee that is for the two
to have no shared row type, no shared token purpose and no shared route.

The zip itself lives in the `exports` document class; the link is an
`EmailToken` (purpose `workspace_export`) — hashed, single use, expiring — so
nothing here stores a secret.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

EXPORT_STATUSES = ("queued", "ready", "failed", "downloaded")
_STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in EXPORT_STATUSES) + ")"


class WorkspaceExport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_exports"
    __table_args__ = (CheckConstraint(_STATUS_CHECK, name="ck_workspace_exports_status"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The owner who asked, and the address the one-time link goes to. Always an
    #: authenticated owner of this workspace — there is no public door here.
    requested_email: Mapped[str] = mapped_column(String(320), nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")
    job_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    #: The `EmailToken` this export's one-time link was issued for. Redeeming a
    #: link resolves THIS row, never "the newest ready export for this person" —
    #: two exports in flight would otherwise have their links crossed.
    download_token_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    # What the worker produced (document class `exports`).
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: How many rows and how many stored objects the zip carries, and how many
    #: objects were referenced but whose bytes are gone — reported, never hidden.
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tables: Mapped[int | None] = mapped_column(Integer, nullable=True)
    documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missing_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set by the artefact purge once the stored zip has been destroyed, so a
    #: request row can still say what happened after its bytes are gone.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
