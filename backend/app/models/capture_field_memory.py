"""Extraction learning-loop memory (E1.5) — per-(vendor x field) correction
history that feeds an ADVISORY hint into the next capture from that vendor.

Harvests the SHAPE of Fleet Fuel's retired `capture_confidence` module (per
`docs/plan/shared/specs/BA_fleet_fuel.md`): per-(supplier x field) accuracy,
advisory hints that never gate. Rebuilt from scratch as an ordinary
tenant-scoped Bid_it table — no bytes carried over from the retired repo
(§10 prohibition).

One row per `(org_id, vendor_name, field)`. `vendor_name` is the SAME
exact-stripped key `app.services.vendors.get_or_create_vendor` matches a
vendor on — a pending (unconfirmed) capture has no `vendor_id` yet, only the
raw captured header `vendor_name` string, so keying on that string is what
lets a fresh capture's fields look up history before any vendor row exists.

§4.19 is absolute here: this table is read-only input to a SUGGESTION
surfaced alongside a field's live provenance
(`app.services.capture_memory.hints_for`, consumed by the capture-review
routes). Nothing that reads this table may write an `ExtractionField` row, an
invoice, or any other financial record — a human must still explicitly apply
a hint through the existing review-correction endpoint for it to take
effect.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class CaptureFieldMemory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capture_field_memory"
    __table_args__ = (
        UniqueConstraint("org_id", "vendor_name", "field", name="uq_capture_field_memory_key"),
        Index("ix_capture_field_memory_org_vendor", "org_id", "vendor_name"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Exact-stripped raw captured header vendor_name — the same key
    # `vendors.get_or_create_vendor` matches on. NOT a human-corrected value
    # (see capture_memory.py for why the raw string is the stable key).
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    field: Mapped[str] = mapped_column(String(40), nullable=False)  # header or line-item field name
    # Times this field was CAPTURED (any run, any status) for this vendor —
    # the accuracy denominator.
    observed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Times a human CORRECTED it during review — the learning signal itself.
    corrected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # The most recent correction pair — what the parser captured vs. what the
    # human changed it to. `last_corrected_value` is the suggestion text.
    last_original_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_corrected_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
