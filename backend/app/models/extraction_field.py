"""Per-field capture provenance (Slice 5f of the data model).

Deepens the extraction run (Slice 5b) from one method-per-document to one row per
top-level field: how each field was obtained (extracted / defaulted / missing),
the captured value, and a confidence slot reserved for the OCR/AI paths. Written
at upload alongside the run; a reviewer sees exactly which fields were auto-filled
and need attention.

Tenant-scoped. Linked to its run by a composite FK `(org_id, extraction_run_id)
→ extraction_runs(org_id, id)` (tenant-safe), ON DELETE CASCADE.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class ExtractionField(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "extraction_fields"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "extraction_run_id"],
            ["extraction_runs.org_id", "extraction_runs.id"],
            name="fk_extraction_fields_run",
            ondelete="CASCADE",
        ),
        Index("ix_extraction_fields_org_run", "org_id", "extraction_run_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extraction_run_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    field: Mapped[str] = mapped_column(String(40), nullable=False)  # invoice_number, issue_date, …
    value: Mapped[str | None] = mapped_column(String(500), nullable=True)  # effective (normalized)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # extracted|defaulted|missing
    # 0..1 confidence set by probabilistic providers (OCR/AI); NULL for deterministic
    # structured parsers — NULL means "exact", not "unknown".
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    # Honest provenance: the raw captured text vs. the cleaned value, and a human's
    # correction if one was made during review (NULL until reviewed).
    original_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Which extraction provider produced the field, and whether it needs a human
    # look (below the confidence threshold, or a non-"extracted" status).
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
