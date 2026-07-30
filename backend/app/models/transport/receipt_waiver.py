"""Receipt-control waivers — the ONLY waivable synthetic-line case (G2.6
slice 3; `BA_fleet_fuel.md` C9, rule "R5 case (a)", R15).

WHY THIS TABLE EXISTS
---------------------
A fuel transaction whose `invoice_ref` is a synthetic `INPUT` placeholder
(`claim_gates.is_synthetic`) means "we captured a spend but have no invoice
for it". Usually that is a note-matching problem to fix (R16's override
table exists for exactly that). But sometimes the invoice genuinely never
arrives — a supplier that never issues one, a lost receipt — and the
transaction can never resolve. `BA_fleet_fuel.md` C9 names the ONE case
where that is acceptable: the supplier has **NO registered invoice at all**
for the claim's refund country. `app.services.transport.waiver.set_waiver`
is the sole writer and enforces exactly that check (reusing
`invoice_match.registered_invoices` — never re-deriving it, the same
drift-prevention discipline `is_synthetic()`/`derive_goods_code()` follow).

WHY THE GRAIN IS `(org, claim, supplier)`, NARROWER THAN THE HARVESTED
`(entity, refund_country, ref_period, supplier)`
------------------------------------------------------------------------
A claim's own grain already IS `(entity, refund_country, ref_period)` —
G2.1's `UNIQUE(org_id, entity_id, refund_country, ref_period)`. Pointing a
waiver at `claim_id` via a composite FK carries the identical key with one
column instead of three, and ties the waiver's lifecycle directly to the
claim it was set on: `ondelete=CASCADE` means a waiver has no reason to
survive its claim (mirrors `VatRefundClaimLine.claim_id`'s own CASCADE FK).

WHY THIS EXCLUDES A SUPPLIER FROM THE CLAIM "BY CONSTRUCTION", NOT AS A
POST-FILTER ON BUILT LINES
------------------------------------------------------------------------
`app.services.transport.claim_lines.build_claim_lines` reads the waived-
supplier set BEFORE grouping transactions and skips a waived supplier's
transactions entirely — they never reach `invoice_match.resolve_invoice_ref`,
never produce an `UNMATCHED` claim line, and never contribute to the frozen
VAT base (C9 verbatim: "dropped from `invs` before the `bad` gate, the
`claim_set`, locks, doc-gate and the frozen VAT base").

Tenant-scoped: composite `(org_id, claim_id)` FK into `vat_refund_claims`,
listed in `app/core/tenant.py::TENANT_MODELS`, RLS-policy'd in the SAME
migration that creates this table.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class VatReceiptWaiver(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One (claim, supplier) receipt-control waiver — `app.services.
    transport.waiver.set_waiver` is the only writer; it refuses a supplier
    that has ANY registered invoice for the claim's refund country
    (R15) and is idempotent on the (claim, supplier) key. `remove_waiver` is
    the only writer that deletes a row (un-waive, only on a `draft` claim).
    """

    __tablename__ = "vat_receipt_waivers"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "claim_id",
            "supplier",
            name="uq_vat_receipt_waivers_key",
        ),
        ForeignKeyConstraint(
            ["org_id", "claim_id"],
            ["vat_refund_claims.org_id", "vat_refund_claims.id"],
            name="fk_vat_receipt_waivers_claim",
            ondelete="CASCADE",
        ),
        Index("ix_vat_receipt_waivers_org_claim", "org_id", "claim_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    # The fuel-card network code (matches FuelTransaction.supplier /
    # Vendor.name — see invoice_match.py's own "what registered means" note).
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
