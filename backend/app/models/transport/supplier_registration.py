"""The per-country supplier legal-entity registration (G3.1 slice 1;
`BA_fleet_fuel.md` R20-R22).

WHY THIS TABLE EXISTS
---------------------
A multi-entity fuel-card network (Eurowag, E100) issues a DIFFERENT local
legal entity per country it operates in — the claim must be filed under
whichever entity actually printed the invoice, never the buyer and never a
factoring/receivables entity the network cedes its invoices to (R20 —
capture reads the seller printed on the document). This table is the
STORAGE half of that rule: one row per (org, supplier, country) ->
(vat_number, entity_name, source). The READING half (actually parsing a
Eurowag/E100 invoice's footer) is `G3.2` (the fuel-card parser registry),
explicitly NOT this order's scope — see `app.services.transport.
supplier_entity`'s module docstring.

WHY `entity_name` IS A PLAIN STRING, NOT A FOREIGN KEY
-----------------------------------------------------------
`IssuerProfile` in this codebase is OUR OWN claimant entity (WO-49's own
reuse — "our legal entity that bought the fuel"). The fuel-card network's
per-country SELLER is a different party this codebase has no existing
master-data table for; inventing a full vendor-side legal-entity registry
now would be scope creep beyond what R21/R22 ask for. A plain string is
the honest, minimal shape.

WHY `source` IS `"admin"` | `"capture"`, AND WHY `"admin"` ALWAYS WINS
------------------------------------------------------------------------
`"admin"` = an explicitly curated marker (`supplier_entity.
set_registration`, the ONLY function that can create or overwrite one).
`"capture"` = seeded by the per-country LEARNING rule (`supplier_entity.
learn_registration`, R22) the FIRST time a capture is confirmed for a
(supplier, country) key with no existing row — it never overwrites an
EXISTING row of EITHER source, so two conflicting captures (or a capture
racing an admin's own edit) can never silently clobber one another. This
mirrors A2.3's "curated data is never silently clobbered" discipline
(vendor bank-detail dual control) translated to a lower-stakes field
(diagnostic/filing metadata, not a payment-redirection vector) — so R22's
own acceptance line is explicit that learning NEVER queues a pending-
change request the way a critical vendor-field edit would.

Tenant-scoped: RLS-policy'd in the SAME migration that creates this table.
No cross-table FK — `supplier` is a free-text fuel-card network code
(matches `FuelTransaction.supplier`/`Vendor.name`'s own exact-match
convention, `invoice_match.py`'s "what registered means"), not a typed
reference to another row.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class SupplierVatRegistration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One (supplier, country) -> (vat_number, entity_name, source)
    registration. `app.services.transport.supplier_entity.set_registration`
    is the only admin-curated writer (always wins); `learn_registration` is
    the only automatic writer, and only ever INSERTS — it never updates an
    existing row of either source (R22)."""

    __tablename__ = "supplier_vat_registrations"
    __table_args__ = (
        UniqueConstraint("org_id", "supplier", "country", name="uq_supplier_vat_registrations_key"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)  # ISO 3166-1 alpha-2
    vat_number: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # "admin" (explicitly curated, always wins) | "capture" (seeded by
    # per-country learning, never overwrites an existing row of either kind).
    source: Mapped[str] = mapped_column(String(12), nullable=False)
