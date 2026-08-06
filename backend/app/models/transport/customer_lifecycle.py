"""The customer lifecycle + per-country activation state (G2.11;
`BA_fleet_fuel.md` §3.F F1/F3, §4's state tables, R44).

WHY THESE TABLES EXIST
----------------------
A VAT refund claim is filed IN THE CLIENT'S NAME under a power of attorney
— filing for a customer who has not signed the contract, provided the
payout bank account, or granted the country PoA is an unauthorised legal
act, not a data-quality bug. The harvested system therefore keyed EVERY
legal/claim gate off `status == 'active'` (F1) and required the refund
country to be SEPARATELY activated (§3.E's second activation bullet, §4:
"Refund country (per customer): (none) → requested → active"). These two
tables are the storage half of that rule; the gate half is
`app.services.transport.customer_lifecycle.enforce_activation`, called by
`lock.submit_claim` (R44's acceptance line: "submitting a claim for a
prospect is refused with the activation message").

WHY A TRANSPORT-LOCAL TABLE KEYED TO `issuer_profiles`, NOT COLUMNS ON IT
-------------------------------------------------------------------------
WO-49's R1 decision reuses `issuer_profiles` as the claimant-entity
registry, but that model is a shared AP/AR-domain table (the invoice-
issuing SELLER profile) — a transport lifecycle column there would leak
vertical-only semantics into the core domain ADR-0023 walls off. The
harvested `customers.db` was itself a store separate from the supplier/
product masters (§4.1), so a dedicated table is the faithful shape — the
same registry precedent as WO-61's `supplier_vat_registrations` and
WO-72's `vat_receipt_controls` (whose composite `(org_id, entity_id)`
RESTRICT FK pattern both tables copy).

WHO WRITES, AND THE TRANSITION SET
----------------------------------
`app.services.transport.customer_lifecycle` is the only writer. Customer
edges are EXACTLY the harvested ones — `(none) → prospect`
(`add_prospect`, idempotent, never downgrades), `prospect → pending`
(`promote_prospect`), `pending ↔ active` (`set_activation`), `active →
inactive` (`set_inactive`; terminal in this slice — no re-onboarding edge
is harvested). Country edges: `(none) → requested` (`request_country`,
idempotent, never downgrades) and `requested ↔ active`
(`set_country_activation`). Every real transition is audited old→new.

Tenant-scoped: both tables RLS-policy'd in the SAME migration that
creates them (master-context §4.2).
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# §3.F F1, verbatim.
CUSTOMER_STATES = ("prospect", "pending", "active", "inactive")
_CUSTOMER_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in CUSTOMER_STATES) + ")"

# §4's state table: "Refund country (per customer): (none) → requested → active".
COUNTRY_STATES = ("requested", "active")
_COUNTRY_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in COUNTRY_STATES) + ")"


class VatCustomerLifecycle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One (org, entity) → lifecycle status row. Absence means the entity
    was never onboarded at all — which the gate treats exactly like a
    not-`active` row (fail CLOSED: an unonboarded entity must never
    out-privilege an onboarded pending one)."""

    __tablename__ = "vat_customer_lifecycles"
    __table_args__ = (
        UniqueConstraint("org_id", "entity_id", name="uq_vat_customer_lifecycles_key"),
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_customer_lifecycles_entity",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_CUSTOMER_CHECK, name="ck_vat_customer_lifecycles_status"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The claimant entity — same issuer_profiles reuse as FuelTransaction.
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)


class VatCountryActivation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One (org, entity, refund country) → activation status row. Only an
    `active` row passes the country half of `enforce_activation`;
    `requested` (documents being gathered) does not, and absence does not
    (fail CLOSED — the harvested order is linear: (none) → requested →
    active, each step an explicit admin click, F3)."""

    __tablename__ = "vat_country_activations"
    __table_args__ = (
        UniqueConstraint("org_id", "entity_id", "country", name="uq_vat_country_activations_key"),
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_country_activations_entity",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_COUNTRY_CHECK, name="ck_vat_country_activations_status"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    # ISO 3166-1 alpha-2 refund country (matches VatRefundClaim.refund_country).
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
