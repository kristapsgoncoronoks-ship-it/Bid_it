"""Document supersession chain (Slice 5g of the data model).

Some document owners hold exactly ONE current file that a re-upload silently
replaces: an issuer's logo (`issuer_profiles.logo_sha256`) and an expense item's
receipt (`expense_items.receipt_sha256`). Overwriting the pointer loses every
prior file's identity — who replaced it, when, and what it was. On an expense
report that may already be submitted or reimbursed, a swapped receipt is exactly
what an audit needs to see.

This is the append-only history for those single-file slots. A *slot* is the
polymorphic `(owner_type, owner_id)` pair (the owner is one of two different
parent tables, so this is deliberately NOT a composite FK — it is a log). Each
upload appends a row with a monotonic per-slot `version`; `is_current` marks the
live one (kept in sync with the owner's `*_sha256` cache, which stays the source
of truth every read path already uses — dual-read, same pattern as the payments
ledger vs `amount_paid`). The Slice 5d `documents` registry still records the
bytes; this records the *sequence* of them for one owner.

Tenant-scoped (org_id + RLS + ORM guard).
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# Slot kinds — the two single-file owners that supersede on re-upload.
OWNER_ISSUER_LOGO = "issuer_logo"
OWNER_EXPENSE_RECEIPT = "expense_item_receipt"


class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        # No gaps or duplicates within a slot.
        UniqueConstraint(
            "org_id", "owner_type", "owner_id", "version", name="uq_document_versions_slot_version"
        ),
        Index("ix_document_versions_slot", "org_id", "owner_type", "owner_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_type: Mapped[str] = mapped_column(String(24), nullable=False)  # issuer_logo | expense_...
    owner_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based, monotonic per slot
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str | None] = mapped_column(String(80), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(200), nullable=True)  # actor email
