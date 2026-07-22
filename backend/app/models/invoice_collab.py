"""Invoice collaboration — internal comments and internal attachments (Phase 08).

The reviewer↔approver thread on a supplier invoice (mirrors `ExpenseComment`) and
internal working documents attached to an invoice during review (contracts, POs,
email screenshots) — kept separate from the invoice's own source document. Bytes
live in object storage (content-addressed, `documents.INVOICE_ATTACHMENTS`); the
row holds the metadata + sha256 pointer.

Both are tenant tables with a composite FK to the same-org invoice.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
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


class InvoiceComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoice_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_invoice_comments_invoice",
            ondelete="CASCADE",
        ),
        Index("ix_invoice_comments_invoice", "org_id", "invoice_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    author_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class InvoiceAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoice_attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_invoice_attachments_invoice",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_invoice_attachments_org_id"),
        Index("ix_invoice_attachments_invoice", "org_id", "invoice_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    internal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    uploaded_by_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
