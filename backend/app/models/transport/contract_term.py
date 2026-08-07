"""The agreed commercial term a fuel supplier can BREACH (G4.5; R41;
`BA_fleet_fuel.md` §4.4, §2.5 "Contract audit").

WHAT THIS TABLE IS
------------------
§4.4's supplier-master schema fragment names it verbatim:

    supplier_discounts (supplier, country, station_like, product_group,
                        expected_discount_eur_l, max_net_eur_l, active)
        <- contract terms

and §2.5's "Contract audit" row fixes what may be stored, verbatim: *"Two term
types only: `expected_discount_eur_l` (rebate that should be applied, €/L) and
`max_net_eur_l` (contracted NET price ceiling, €/L). ... **No volume-tier /
stepped-rebate / annual-bonus / card-fee modelling.**"* That closing sentence is
a constraint, not a nicety: it is why this table has exactly two figure columns
and no tier/threshold/bonus structure. A term the model cannot express is a term
this product does not audit — stated, never approximated.

WHY A TRANSPORT-LOCAL REGISTRY, NOT A COLUMN ON THE AP `Vendor`
----------------------------------------------------------------
This codebase has no transport supplier master: a fuel transaction's `supplier`
is a free-text fuel-card network code (`fuel_transaction.py`). The established
precedent for per-supplier transport configuration is WO-61's
`supplier_vat_registrations` and WO-72's `vat_supplier_cadences` — a
transport-local tenant table keyed on that same free-text code, never a column
bolted onto `Vendor` (ADR-P3 rule 1). This follows it.

THE GRAIN, AND `station_like`
------------------------------
The natural key is the harvested one: `(org, supplier, country, station_like,
product_group)`. `station_like` is a LIKE pattern over `FuelTransaction.station`
("the city dimension", §4.2 row 6) and is `""` — an empty STRING, never NULL —
when the term applies to every station. Empty string rather than NULL for the
reason `receipt_control.py` and `fuel_transaction.py` both document: `NULL <>
NULL` under a UNIQUE constraint lets duplicate rows through silently, and a
duplicate term here would double-count real money.

Because a wildcard term and a station-specific term can both match one line,
`contract_audit` applies exactly ONE — most specific wins (see that module's
docstring for the full rule and why double-counting would be a wrong money
figure). That precedence is a DOCUMENTED INTERPRETATION; the spec supplies the
column but not the tie-break.

WHY BOTH FIGURE COLUMNS ARE NULLABLE, WITH A CHECK
---------------------------------------------------
The two term types are independent: a contract may fix a rebate, a price
ceiling, or both. A row asserting NEITHER is not a term at all — it would make
every line it matches audit against nothing while shadowing a broader term it
out-ranks, so the CHECK constraint refuses it at the database level (the
defense-in-depth discipline of `goods_code <> '9'` and the `product_group`
CHECK: the service is the real gate, but a malformed value must never reach
storage through a raw path).

PRECISION: `Numeric(12, 4)`, NOT `Numeric(14, 2)`
--------------------------------------------------
These are €/L RATES, not amounts. Cents precision would be wrong by an order of
magnitude against the harvested tolerance itself (`TOLERANCE = 0.005 €/L`) and
against real contracted discounts (§5.1's own examples run 0.08–0.23 €/L, and
TFC's hub tier is 0.205). Four decimal places carry every harvested value
exactly with a decade of headroom under the tolerance. Money AMOUNTS elsewhere
stay `Numeric(14, 2)`; nothing in this table is an amount.

Tenant-scoped: registered in `app/core/tenant.py::TENANT_MODELS`, RLS-policy'd
in the SAME migration that creates it (master-context §4.2). The composite
`(org_id, id)` unique exists so a future table can FK to a term (§4.3's
convention); no code references it today.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.transport.fuel_transaction import PRODUCT_GROUPS

# The term applies to every station unless a pattern narrows it (see the
# module docstring for why this is "" and not NULL).
ALL_STATIONS = ""

_PRODUCT_GROUP_CHECK = "product_group IN (" + ", ".join(f"'{g}'" for g in PRODUCT_GROUPS) + ")"

# §2.5's "two term types only" — a row must assert at least one of them.
_TERM_PRESENT_CHECK = "expected_discount_eur_l IS NOT NULL OR max_net_eur_l IS NOT NULL"


class VatSupplierContractTerm(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One agreed commercial term for a (supplier, country, station pattern,
    product group). `contract_audit.set_term` / `remove_term` are its only
    writers (audited old→new); `contract_audit.audit` reads it and NEVER
    writes. Basis: NET EUR/L, final — VAT excluded, rebates applied
    (`BA_fleet_fuel.md` §3.G G1 / R49)."""

    __tablename__ = "vat_supplier_contract_terms"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "supplier",
            "country",
            "station_like",
            "product_group",
            name="uq_vat_supplier_contract_terms_key",
        ),
        UniqueConstraint("org_id", "id", name="uq_vat_supplier_contract_terms_org_id_id"),
        CheckConstraint(_PRODUCT_GROUP_CHECK, name="ck_vat_supplier_contract_terms_product_group"),
        CheckConstraint(_TERM_PRESENT_CHECK, name="ck_vat_supplier_contract_terms_has_a_term"),
        Index("ix_vat_supplier_contract_terms_lookup", "org_id", "supplier", "country"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The fuel-card network code — matches `FuelTransaction.supplier` exactly
    # (admin-curated master data, never fuzzy-matched: the R21 marker-only
    # discipline).
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    # ISO 3166-1 alpha-2 country of SUPPLY — `FuelTransaction.country`.
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    # A LIKE pattern over `FuelTransaction.station`; "" = every station.
    station_like: Mapped[str] = mapped_column(String(120), nullable=False, default=ALL_STATIONS)
    # One of `fuel_transaction.PRODUCT_GROUPS` (CHECK-constrained above).
    product_group: Mapped[str] = mapped_column(String(20), nullable=False)

    # §2.5's two term types. €/L rates, NOT amounts — see the module docstring
    # for why the precision is 4 decimal places.
    expected_discount_eur_l: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    max_net_eur_l: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    # §4.4's own `active` column: a term is deactivated, never deleted, when a
    # contract lapses — so a past period can still be audited against the terms
    # that applied to it (the same "kept forever" reasoning §2.5 gives for
    # `advertised_prices`). `remove_term` exists for a mis-keyed row.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
