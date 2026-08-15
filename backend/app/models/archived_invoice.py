"""The platform archive — a SEALED store for invoices past their recycle bin.

See `docs/design/platform-archive.md`. When the 30-day bin expires, the invoice
row is destroyed from `invoices` and a copy lands here instead.

WHY A SEPARATE TABLE RATHER THAN A FLAG
---------------------------------------
An `is_archived` column on `invoices` would mean every query, export and support
tool in the product can reach archived data, and one forgotten filter surfaces a
record the client believes they deleted. The recycle bin's own step-1 guard
exists because 19 query sites could not be trusted to each remember a rule; a
separate table makes reaching archived data an ACT rather than an accident.

WHY IT IS STILL TENANT-SCOPED IN THE ORDINARY WAY
-------------------------------------------------
Owner decision: the CLIENT's company owner can read their own organisation's
archive. That makes this table's `org_id` filter a PRIMARY control read by every
client owner, not a backstop read by a handful of named staff — so it is
registered in `tenant.TENANT_MODELS` and gets the same central guard as the live
tables.

WHAT IT HOLDS
-------------
The record AND a pointer to the source document (owner decision). The invoice's
scalar fields are stored as columns because they are what a person searches by;
the line items are stored as a JSON payload because nothing queries them — they
are read back whole or not at all, and a second archived-line-items table would
be a second thing to keep in step for no query benefit.

`source_sha256` points at bytes in the `UPLOADS` document store that the bin
purge deliberately does NOT delete any more: the PDF is what proves anything to a
tax authority years later, which is most of the reason to archive at all.

EXPIRY IS STORED, NOT COMPUTED
------------------------------
`expires_at` is stamped when the row is written rather than derived from the
current retention setting on read. Deriving it would mean shortening the setting
retroactively destroys records that were already archived under a longer promise
— the client is paying for a period, and lowering a number in a settings table
must never reach backwards. Extending retention is therefore an explicit
operation over existing rows, which is the correct shape for a paid extension.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class ArchivedInvoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One invoice, kept after its recycle bin expired."""

    __tablename__ = "archived_invoices"
    __table_args__ = (
        # The client-owner archive screen: newest first, for one org.
        Index("ix_archived_invoices_org_archived", "org_id", "archived_at"),
        # The expiry sweep, and the "what is about to leave" notice that must
        # run before it.
        Index("ix_archived_invoices_expires", "expires_at"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The id the invoice had while it was live. NOT a foreign key — the row it
    # pointed at is gone, which is the whole point of this table. Kept so an
    # audit event, a support ticket or a client's own export can be tied back to
    # the record it describes.
    original_invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)

    invoice_number: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    vendor_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    # Denormalised: the vendor may itself be deleted before this row expires, and
    # an archive that cannot say who the invoice was from is not much of a record.
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # Line items, whole. Read back or not at all; nothing queries into them.
    line_items_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # The source document, retained with the record (owner decision). NULL when
    # the invoice was keyed in by hand and never had one.
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Who deleted it and when, carried over from the bin so the archive answers
    # "how did this get here" without a join to a table that may itself age out.
    original_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    original_deleted_by: Mapped[str | None] = mapped_column(String(320), nullable=True)

    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Stamped at write time — see the module docstring on why this is stored
    # rather than derived.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
