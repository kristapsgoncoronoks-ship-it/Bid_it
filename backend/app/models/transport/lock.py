"""The one-invoice-one-submission lock (G2.2; `BA_fleet_fuel.md` section 3.C5/C7,
R4/R5).

WHY THIS TABLE EXISTS
---------------------
WO-49 shipped the claim grain and WO-50 shipped the typed transaction line, but
nothing yet stops the same invoice being claimed twice — a duplicate submission,
a race between two operators, or the same invoice pulled into both a quarterly
and an annual claim. `VatClaimedInvoice`'s `UNIQUE(org_id, entity_id,
refund_country, supplier, invoice_ref)` IS the lock (`BA_fleet_fuel.md` section
4.3's schema fragment, verbatim: *"vat_claimed_invoices (entity, refund_country,
supplier, invoice_ref) UNIQUE = THE LOCK. Released ONLY by withdraw_claim."*).

WHY `entity_id`/`refund_country` ARE DENORMALIZED HERE, NOT READ THROUGH
`claim_id`
--------------------------------------------------------------------------
The constraint must span EVERY claim, not just the one that currently holds a
given row: a second, later claim for a different period must still collide
with an invoice already locked by a DIFFERENT claim. Reading `entity_id`/
`refund_country` through a join to `vat_refund_claims` would make the
uniqueness constraint effectively `(claim_id, supplier, invoice_ref)` in
spirit — trivially satisfied by two different claims locking the identical
invoice, which is exactly the bug R4 exists to prevent. Denormalizing both
columns onto this table is what makes the natural key invoice-grained across
the WHOLE tenant, not claim-grained.

WHY THE WIDENED KEY IS `(org_id, entity_id, refund_country, supplier,
invoice_ref)`, NOT THE LITERAL BA TEXT
------------------------------------------------------------------------------
`BA_fleet_fuel.md`'s R4/R5 rows and section 4.3 both write the key without
`org` — Fleet Fuel was single-tenant. `ARCH_plan.md`'s own G2.2 description
(line 874) already states it as `UNIQUE(org, entity, refund_country, supplier,
invoice_ref)`, matching this codebase's standing convention of prefixing every
tenant-scoped uniqueness constraint with `org_id` (identical widening WO-49
applied to the claim grain and WO-50 to the transaction natural key). This is
the ARCH_plan wording, not an invented addition — see the WO-51 work order's
citation-check note for the full verification.

WHY THREE COMPOSITE FKs, TWO RESTRICT AND ONE CASCADE
------------------------------------------------------
- `(org_id, claim_id) -> vat_refund_claims(org_id, id)`, `ondelete=CASCADE` —
  mirrors `vat_claim_lines.claim_id` (WO-49); moot in practice, since no code
  path in this codebase deletes a claim.
- `(org_id, entity_id) -> issuer_profiles(org_id, id)`, `ondelete=RESTRICT` —
  matching every other transport table's entity FK (WO-49/WO-50): an org's
  own legal entity must not be deletable out from under a live lock.
- `(org_id, fuel_transaction_id) -> fuel_transactions(org_id, id)`,
  `ondelete=RESTRICT` — WO-50 built the `uq_fuel_transactions_org_id_id`
  composite unique constraint specifically so this FK could exist. One
  REPRESENTATIVE transaction row per lock (the natural key is invoice-grained;
  `fuel_transactions` is line-grained — one invoice can span several
  transaction rows). Protecting EVERY transaction row sharing an
  `invoice_ref` from deletion, not just this anchor, is a service-level
  concern the future close/re-close guard (G1.4) must add on top — see the
  WO-51 work order's Implementation guidance §2 for the full boundary note;
  this FK alone does not solve it.

Tenant-scoped: composite `(org_id, id)` FK targets, listed in
`app/core/tenant.py::TENANT_MODELS`, RLS-policy'd in the SAME migration that
creates this table.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class VatClaimedInvoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The lock row — one per claimed invoice. See the module docstring for why
    `entity_id`/`refund_country` are denormalized rather than read through
    `claim_id`, and why the FK into `fuel_transactions` targets one
    representative row.

    `app.services.transport.lock.submit_claim` is the ONLY function that
    inserts a row here (a plain ORM INSERT, never an upsert — a lost race
    raises `IntegrityError` at flush, aborting the whole submit transition).
    `app.services.transport.lock.withdraw_claim` is the ONLY function that
    deletes one (R5 — "only an explicit withdraw releases locks").
    """

    __tablename__ = "vat_claimed_invoices"
    __table_args__ = (
        # THE LOCK (R4) — see module docstring "WHY THE WIDENED KEY IS...".
        UniqueConstraint(
            "org_id",
            "entity_id",
            "refund_country",
            "supplier",
            "invoice_ref",
            name="uq_vat_claimed_invoices_lock",
        ),
        ForeignKeyConstraint(
            ["org_id", "claim_id"],
            ["vat_refund_claims.org_id", "vat_refund_claims.id"],
            name="fk_vat_claimed_invoices_claim",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_claimed_invoices_entity",
            ondelete="RESTRICT",
        ),
        # A fuel transaction with an active lock must not be deletable — see
        # module docstring for what this FK deliberately does and does not
        # solve (one anchor row, not every row sharing an invoice_ref).
        ForeignKeyConstraint(
            ["org_id", "fuel_transaction_id"],
            ["fuel_transactions.org_id", "fuel_transactions.id"],
            name="fk_vat_claimed_invoices_fuel_transaction",
            ondelete="RESTRICT",
        ),
        Index("ix_vat_claimed_invoices_org_claim", "org_id", "claim_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which claim currently holds this lock. FK enforced via the composite
    # ForeignKeyConstraint above (org-safe).
    claim_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    # Denormalized off the claim at acquisition time — see module docstring
    # "WHY entity_id/refund_country ARE DENORMALIZED HERE...". Never read
    # through claim_id: the whole point is that the constraint spans every
    # claim, not just the one that happens to hold this row.
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    refund_country: Mapped[str] = mapped_column(String(2), nullable=False)
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    invoice_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    # One representative fuel_transactions row for this invoice — see module
    # docstring. FK enforced via the composite ForeignKeyConstraint above.
    fuel_transaction_id: Mapped[str] = mapped_column(GUID(), nullable=False)
