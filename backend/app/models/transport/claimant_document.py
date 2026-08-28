"""The claimant document store WITH AN EXPIRY (G2.10 slice 2;
`BA_fleet_fuel.md` §3.E's `check_type="document"` rules and §3.F F3's
`country_requirements` / `DOC_KINDS` catalogue, R45).

WHY A NEW TABLE WHEN `documents` ALREADY EXISTS
------------------------------------------------
`app.models.document.Document` (Slice 5d) is a content-addressed REGISTRY:
one row per distinct `(org, sha256, kind)` so a workspace can enumerate what
bytes it holds. It deliberately has no owner and no validity window — it
answers "what do we store", not "does THIS claimant hold a currently-valid
power of attorney for ES". `_has_doc` needs the second question, and a
`valid_until` column on a content-addressed registry would be a lie the first
time two claimants uploaded the same template.

The BYTES still go through the one choke point (`documents.store`), so every
upload here also registers in that registry. This table is the OWNERSHIP and
VALIDITY overlay on top of it, not a second vault.

WHY A TRANSPORT-LOCAL TABLE KEYED TO `issuer_profiles`
-------------------------------------------------------
Exactly `VatCountryActivation`'s precedent, for exactly its reason: a power of
attorney addressed to a national tax authority is vertical-only semantics that
ADR-0023 walls out of the core AP/AR domain. Composite `(org_id, entity_id)`
RESTRICT FK, the same shape `vat_customer_lifecycles` and
`vat_country_activations` use.

(`IssuerProfile.nace_code` goes the OTHER way, onto the core row, and the
distinction is deliberate: a NACE code is neutral company master data of the
same class as `registration_number`/`vat_number`, which the checklist's own
`_verify_customer_data` already reads from that row. What ADR-0023 keeps out
is transport STATE, not a company's own identifying attributes.)

WHY `country` IS `""` RATHER THAN NULL FOR A CUSTOMER-SCOPE DOCUMENT
----------------------------------------------------------------------
A contract or trade-register extract is held once per claimant; a PoA is held
per refund country (§3.E's `scope` column). Both live here, and the unique key
has to cover both. In Postgres a NULL never equals a NULL, so a nullable
`country` would let the same contract be inserted any number of times while
appearing constrained. `""` is a real value that compares, and the CHECK
constraint keeps it from ever holding a one-letter fragment.

WHY THE UNIQUE KEY CARRIES `sha256`
-------------------------------------
`(org_id, entity_id, kind, country, sha256)`. Re-uploading the SAME bytes is
idempotent — the row is touched, not duplicated. A RENEWAL is different bytes
and gets its own row, which is what keeps the history a renewal needs: the
expired PoA stays visible next to the one that replaced it, so an operator can
see that the gap was closed rather than merely that it is closed now.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# §3.F F3's catalogue, plus the two kinds §3.E's own rule table references
# (`signed_contract`, `trade_registry`). One list — the CHECK constraint, the
# service and the checklist's `reference` column all read it.
DOC_KINDS = (
    "power_of_attorney",
    "vat_certificate",
    "tax_mandate",
    "fleet_list",
    "company_extract",
    "signatory_id",
    "signed_contract",
    "trade_registry",
)
_KIND_CHECK = "kind IN (" + ", ".join(f"'{k}'" for k in DOC_KINDS) + ")"

# `""` (customer scope) or a 2-letter ISO code (country scope) — never a
# one-character fragment. See the module docstring.
_COUNTRY_CHECK = "country = '' OR length(country) = 2"


class VatClaimantDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One held document for one claimant. Absence is "not held"; a row whose
    `valid_until` has passed is "held but expired" — both fail the checklist,
    and the checklist says which, because "no PoA on file" and "the PoA expired
    on 2026-03-31" are different jobs for the operator."""

    __tablename__ = "vat_claimant_documents"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "entity_id",
            "kind",
            "country",
            "sha256",
            name="uq_vat_claimant_documents_key",
        ),
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_claimant_documents_entity",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_KIND_CHECK, name="ck_vat_claimant_documents_kind"),
        CheckConstraint(_COUNTRY_CHECK, name="ck_vat_claimant_documents_country"),
        Index("ix_vat_claimant_documents_org_entity", "org_id", "entity_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # "" for a customer-scope document; ISO 3166-1 alpha-2 for a country one.
    country: Mapped[str] = mapped_column(String(2), nullable=False, server_default="")
    # The bytes, in object storage under prefix "claimant-documents".
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str | None] = mapped_column(String(80), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # NULL `valid_until` means "no stated expiry", not "expired" — a trade
    # register extract usually has none, a PoA usually does.
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
