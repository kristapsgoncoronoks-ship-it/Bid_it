"""The typed fuel-transaction line (G1.2; `BA_fleet_fuel.md` section 4.2 "The
core business entity — a validated fuel TRANSACTION", section 8.1 items 4-6).

WHY THIS TABLE EXISTS AT ALL — the two Fleet Fuel bugs it replaces
--------------------------------------------------------------------
Fleet Fuel declares the same field list, verbatim, in two modules and defaults
it again in a third (a positional row list, no ORM, every consumer hard-codes
indices) — `BA_fleet_fuel.md` section 8.1 item 4. Worse: `transactions` has
**no primary key and no unique constraint**; idempotency is "DELETE the whole
month, re-INSERT everything" (section 8.1 item 6). `FuelTransaction` is one
typed SQLAlchemy model (no duplicated schema to drift) with a real natural key
(see below) so duplicate suppression is structural, not a side effect of a
period-scoped DELETE.

The THIRD bug — the overloaded `note` column, which simultaneously carried an
invoice number, a rebate explanation, and a cash-at-pump flag, and was also
the field `_resolve_inv` pattern-matched an invoice on (section 8.1 item 5) —
is why this model has separate `invoice_ref` (the raw matching reference) and
`provenance_note` (free-text lineage) columns instead of one.

WHY THE NATURAL KEY IS `(org_id, entity_id, supplier, period, line_seq)`,
NOT SOMETHING KEYED ON `invoice_ref`
--------------------------------------------------------------------------
Section 8.1 item 6 says only "add a natural key (supplier, invoice line
identity)" — it does not spell out what "invoice line identity" means, and the
obvious literal reading (key on `invoice_ref`) does not hold up: `invoice_ref`
is nullable here (many fuel-card feeds arrive as a per-card statement, not
per-invoice, so the invoice reference is often unresolved AT INGESTION time —
resolving it is a future note-matching service, G2.4/G2.5). Keying uniqueness
on a nullable column is unsound: `NULL <> NULL` under a UNIQUE constraint lets
duplicate NULLs through silently on every SQL dialect this project targets
(SQLite, Postgres), which would make the "structural" guarantee this order
exists to deliver false exactly when `invoice_ref` is unknown — the common
case, not the edge case.

`period` (`YYYY-MM`) is always known at ingestion — it was Fleet Fuel's own
partition/idempotency unit ("period... DELETE-by-period is the idempotency
unit", section 4.2). `line_seq` is the 1-based position of this row within its
SOURCE FILE for that `(entity, supplier, period)` group, assigned by the
CALLER (the future parser/ingestion pipeline) deterministically: re-parsing
the identical file yields the identical sequence, so calling
`app.services.transport.fuel_ingest.ingest_transaction()` twice with the same
file is idempotent by construction, while a corrected/appended file adds new
`line_seq` values without colliding with previously-ingested ones. This is a
documented INTERPRETATION of an underspecified BA phrase, not an invented
requirement (see the G1.2 work order's Implementation guidance §2) — flagged
here so a future reader does not mistake it for a verbatim harvested rule the
way `is_synthetic()` is.

WHY `invoice_id` IS A NULLABLE FK TO `invoices`, NOT TO `vat_claim_lines`
-----------------------------------------------------------------------------
ADR-P3 rule 1 / `VAT_HARVEST.md` E.2, verbatim: "Fuel detail lives in
`fuel_transactions` with a nullable FK to the AP invoice it was captured
from." `vat_claim_lines.invoice_id` (WO-49) already points at the SAME
`invoices` table. This is how the two transport tables "eventually relate"
without a direct cross-reference between them: a future materialization
service resolves `invoice_ref` on both, and anything that needs to join a
claim line back to its originating transactions does so THROUGH `invoices`,
never via a new `fuel_transactions` <-> `vat_claim_lines` FK (which ADR-P3
does not define and this order does not invent). No code in this order
populates `invoice_id` — it stays NULL until the future note-matching
service resolves it.

WHY `fx_source` IS THE PLATFORM'S 4-VALUE ENUM, NOT FLEET FUEL'S 3-VALUE ONE
--------------------------------------------------------------------------------
Fleet Fuel's own `fx_source` is `eur | ecb | none`. This codebase already has
a platform-wide FX provenance convention (`app/models/fx.py::FxSource`,
`eur | stated | ecb | unknown`), matching master-context invariant §4.15
verbatim. ADR-P3 rule 4 ("Transport reuses the platform floor unchanged")
means the platform convention wins here, not a literal harvest of Fleet
Fuel's narrower one — `FX_SOURCE_CHECK` is reused as-is (`app/models/fx.py`),
not re-declared.

`qty` is DELIBERATELY NOT money-quantized (section 4.2 row 9; master-context
§4.9's own carve-out) — it is the €/L denominator. Every monetary column IS
quantized (`app.core.money.q2`, ROUND_HALF_UP) by the ingestion service before
the row is built; this model itself applies no rounding (Numeric storage
columns, per master-context convention).

Tenant-scoped: composite `(org_id, id)` FK targets, listed in
`app/core/tenant.py::TENANT_MODELS`, RLS-policy'd in the SAME migration that
creates this table. The `(org_id, id)` unique constraint below is ALSO the
future FK target for the G2.2 lock table (`vat_claimed_invoices`) — no code in
this order references it; it exists so that future migration can.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.fx import FX_SOURCE_CHECK

# WO-88 — the FX provenance CONSISTENCY invariant, as SQL.
#
# `FX_SOURCE_CHECK` above constrains only the value DOMAIN; it says nothing
# about the combination, so until WO-88 a row could assert both "no rate was
# available" (`fx_source='unknown'`, which `app/models/fx.py` defines as *"EUR
# figure is NULL, never a guessed number"*) and a non-NULL `net_eur`. Two
# clauses, one per way that assertion can be false:
#
#   1. `unknown` beside a stored EUR figure. `net_eur` is NOT NULL here, so on
#      this table the honest outcome of "no rate" is NO ROW — the branch
#      `statement_ingest` already takes. The `net_eur IS NULL` disjunct is kept
#      deliberately even though it can never fire today: it states the
#      invariant the way ADR-0010 states it, so a future order that makes the
#      EUR columns nullable inherits a constraint that still means the right
#      thing instead of one that has silently become wrong.
#   2. a non-EUR document currency with NO provenance at all — master-context
#      §4.14's *"it never labels a foreign amount EUR"*. A EUR row with a NULL
#      `fx_source` stays legal: EUR is the identity and involves no rate.
#
# Plain portable SQL, the `FX_SOURCE_CHECK` precedent's form: no `IS DISTINCT
# FROM` (SQLite gained it only in 3.39) and `upper()`, which is immutable —
# and therefore legal inside a CHECK — on both SQLite and PostgreSQL.
FX_PROVENANCE_CHECK = (
    "(fx_source IS NULL OR fx_source <> 'unknown' OR net_eur IS NULL)"
    " AND (upper(currency) = 'EUR' OR fx_source IS NOT NULL)"
)

# The exact 7-category set, `BA_fleet_fuel.md` section 4.2 row 8, verbatim.
# `derive_product_group()` (app/services/transport/product_group.py) is the
# ONLY code allowed to compute this value — never re-derive the precedence
# elsewhere (mirrors why `is_synthetic()` is centralized, WO-49).
PRODUCT_GROUPS = (
    "Diesel",
    "HVO",
    "AdBlue",
    "Parking",
    "Toll/Fees",
    "Promo adj",
    "Service/Other",
)
_PRODUCT_GROUP_CHECK = "product_group IN (" + ", ".join(f"'{g}'" for g in PRODUCT_GROUPS) + ")"


class FuelTransaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One validated fuel-card / toll-network line item, per
    `BA_fleet_fuel.md` section 4.2's canonical schema. See the module
    docstring for why the natural key is `(org_id, entity_id, supplier,
    period, line_seq)` and why `invoice_id` targets `invoices`, not a
    transport-internal table.
    """

    __tablename__ = "fuel_transactions"
    __table_args__ = (
        # The natural key — see module docstring "WHY THE NATURAL KEY IS...".
        UniqueConstraint(
            "org_id",
            "entity_id",
            "supplier",
            "period",
            "line_seq",
            name="uq_fuel_transactions_natural_key",
        ),
        # Composite FK target for the (future) vat_claimed_invoices lock
        # table (G2.2) — the master-context convention (§4.3) for every
        # cross-table reference inside a tenant.
        UniqueConstraint("org_id", "id", name="uq_fuel_transactions_org_id_id"),
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_fuel_transactions_entity",
            ondelete="RESTRICT",
        ),
        # ADR-P3 rule 1 — the ONE nullable FK from transport INTO the AP
        # invoice table (never the reverse). SET NULL: an invoice deletion
        # must not delete transaction history, only unlink it.
        ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_fuel_transactions_invoice",
            ondelete="SET NULL",
        ),
        # Defense-in-depth: `derive_product_group()` is the real source of
        # truth, but a malformed value must never reach storage via a raw
        # path (same "assert the absence" discipline as WO-49's
        # goods_code <> '9' constraint).
        CheckConstraint(_PRODUCT_GROUP_CHECK, name="ck_fuel_transactions_product_group"),
        CheckConstraint(FX_SOURCE_CHECK, name="ck_fuel_transactions_fx_source"),
        # WO-88 — the value domain above is not enough; the COMBINATION has to
        # hold too. See FX_PROVENANCE_CHECK's comment, and
        # `fuel_ingest._require_fx_provenance` for the same rule at the writer
        # (both layers, always).
        CheckConstraint(FX_PROVENANCE_CHECK, name="ck_fuel_transactions_fx_provenance"),
        Index("ix_fuel_transactions_org_entity", "org_id", "entity_id"),
        Index("ix_fuel_transactions_org_period", "org_id", "period"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The claimant's own legal entity — "our" side of the purchase, see
    # app/models/transport/vat_claim.py for the same reuse-of-issuer_profiles
    # rationale (BA_fleet_fuel.md row 985: "Our legal entity that bought the
    # fuel"). FK enforced via the composite ForeignKeyConstraint above.
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)

    # Fuel-card issuer / toll-network code (Q8, BP, TFC, E100, MOEVE, DKV, ...
    # — free text, not an enum: the supplier set is admin-curated master data
    # in `supplier_master`-equivalent territory, not a fixed platform list).
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    # "YYYY-MM" — the accounting month. Retained as a first-class column (not
    # derived from txn_date) because it is HALF of the natural key and the
    # future G1.3/G1.4 monthly close's period-scoped delete unit — see module
    # docstring.
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    # 1-based position of this row within its source file for
    # (entity, supplier, period) — see module docstring "WHY THE NATURAL
    # KEY IS...". Caller-assigned, never inferred by counting existing rows
    # (that would break idempotency on a retry).
    line_seq: Mapped[int] = mapped_column(Integer, nullable=False)

    # ISO 3166-1 alpha-2 of the country of SUPPLY = the VAT jurisdiction
    # (drives VAT rate, refund claim country, excise eligibility) — per-line
    # for a multi-country supplier, section 4.2 row 2.
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    # Vehicle / card identity — composite and supplier-specific (card,
    # card/plate, plate, vehicle id). Deliberately NOT normalized into a
    # vehicle master here (section 4.2 row 3) — that is out of this order's
    # scope and not required by G1.2.
    vehicle_ref: Mapped[str] = mapped_column(String(60), nullable=False)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Free text; "" when unavailable (never NULL — section 4.2 row 5's own
    # convention, kept here even though line_seq — not txn_time — carries the
    # natural key's uniqueness burden).
    txn_time: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    # Station / point of sale — "the city dimension" (section 4.2 row 6).
    station: Mapped[str] = mapped_column(String(120), nullable=False)
    # Product name AS PRINTED on the source document (supplier vocabulary,
    # any language) — the raw input to derive_product_group().
    product: Mapped[str] = mapped_column(String(120), nullable=False)
    # Normalised group — see PRODUCT_GROUPS / the CHECK constraint above.
    # Computed by app.services.transport.product_group.derive_product_group();
    # this column never accepts a value outside PRODUCT_GROUPS, even via a
    # raw path (defense-in-depth, same discipline as WO-49's goods_code<>'9').
    product_group: Mapped[str] = mapped_column(String(20), nullable=False)

    # Litres (fuel) / units (AdBlue, parking, tolls). DELIBERATELY NOT
    # money-quantized — see module docstring. Can be 0 (promo correction
    # lines, section 4.2 row 9).
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Amounts in document currency — q2-quantized by the ingestion service.
    net_local: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    vat_local: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    gross_local: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # Document net converted to EUR — the as-invoiced net.
    net_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Reclaimable VAT in EUR — summed per (entity, country, period) into the
    # (future) claim; the VAT-refund north star (section 4.2 row 15).
    vat_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Effective net EUR after ALL rebate layers, INCLUDING off-invoice ones
    # (the two-tier discount model, section 4.2 "net_eur_eff" note). Defaults
    # to net_eur when no off-invoice rebate applies — the ingestion service
    # sets this explicitly rather than relying on a DB default, so a caller
    # that forgets it is a visible bug, not a silent identity.
    net_eur_eff: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # Split from Fleet Fuel's overloaded `note` column (section 8.1 item 5).
    # Nullable: often unresolved at ingestion time — see module docstring for
    # why this could not be the natural key. A future note-matching service
    # (G2.4/G2.5) resolves this to `invoice_id`.
    invoice_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Free-text lineage/audit trail — rebate explanation, cash-at-pump flag,
    # capture provenance. The OTHER half of the split `note` column; never
    # re-merged with invoice_ref.
    provenance_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The RESOLVED AP invoice this line was captured from — NULL until a
    # future service resolves invoice_ref. See module docstring "WHY
    # invoice_id IS A NULLABLE FK TO invoices...".
    invoice_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)

    # FX provenance quadruple — app/models/fx.py's platform-wide convention
    # (see module docstring "WHY fx_source IS...").
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    fx_ecb_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    fx_ecb_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fx_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
