"""The VAT refund claim aggregate root + its (future-frozen) lines (R1, R2, R13;
ADR-P3 "the one Fleet Fuel invariant that is translated, not copied").

WHY THE GRAIN IS WHAT IT IS
----------------------------
Fleet Fuel keys a claim `(entity, refund_country, ref_period)` — one refund
application per *our own legal entity* per *refund-issuing member state* per
*filing period* (Art. 5 Dir. 2008/9/EC: a taxable person not established in
the refunding state reclaims VAT there via its OWN state's portal, one
application per period). `period` is one of `{YYYY-Qn, YYYY-YEAR}` per Art. 16
(minimum 3 months, maximum 1 calendar year, shorter only as the remainder of a
year). `UNIQUE(org_id, entity_id, refund_country, ref_period)` is the load-
bearing constraint: creating two claims with the same key must upsert, never
duplicate (R1 acceptance test).

WHY `entity_id` POINTS AT `issuer_profiles`, NOT A NEW TABLE
--------------------------------------------------------------
Fleet Fuel's "entity" is defined verbatim in the harvested spec as *"Our
legal entity that bought the fuel — the VAT-registered claimant"*
(`BA_fleet_fuel.md` row 985). Bid_it already carries exactly that concept:
`IssuerProfile` is "a legal entity this org ISSUES invoices as" — name, VAT
number, registration number, address, country, IBAN — and an org may already
register MULTIPLE such entities (the multi-issuer registry, WO-? / the
Fleet-Fuel "MULTIPLE issuer companies" pattern the harvest doc explicitly
says NOT to re-build because "Bid_it's AR engine is better and already
built", `docs/plan/shared/VAT_HARVEST.md` E.5). Reusing it here is exactly
ADR-P3 rule 2 in spirit — no second legal-entity registry, no duplicated VAT/
address capture. The FK is a plain database constraint (referential
integrity), not a query join: `app/services/transport/*` still reads
`issuer_profiles` data through a service call, never a hand-written join
(ADR-P3 rule 2 / VAT_HARVEST E.2).

WHY CLAIM LINES ARE A SEPARATE, MATERIALIZED-LATER TABLE
-----------------------------------------------------------
Fleet Fuel derives claim lines LIVE from `transactions` at read time
(`invoice_lines()` GROUP BY note/product_group) and protected the *filed*
claim only by keeping the whole legal database physically separate from the
monthly-rebuilt analytics database. ADR-P3 translates that into something
strictly stronger for Postgres: a claim MATERIALIZES AND FREEZES its own
`VatRefundClaimLine` rows at first entry to a locking state (submission),
alongside the frozen `vat_eur`/`vat_local`/`fee_pct`/`fee_min`/`fee_eur` on
the claim itself. Once frozen, nothing reads through to the transaction data
any more, so a re-close of the period cannot change what was filed. THIS
ORDER (WO-49) ships only the schema for that freeze — `frozen_at` stays NULL
until the (future) submission service stamps it; no code here populates a
line yet.

Both tables are tenant-scoped: composite `(org_id, id)` FK targets, listed in
`app/core/tenant.py::TENANT_MODELS`, RLS-policy'd in the SAME migration that
creates them (`alembic/versions/<rev>_transport_vat_claim_grain.py`).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# Engine status — the coarse state driving (future) locks/fees. Kept as a plain
# string column (not a DB enum) so a new value never needs a migration, mirroring
# every other lifecycle column in this codebase (Invoice.status, IssuedInvoice
# status, etc).
CLAIM_STATUSES = ("draft", "submitted", "approved", "paid", "withdrawn", "rejected")


class VatRefundClaim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The claim aggregate root — `(org, entity, refund_country, ref_period)`.

    Carries the fields that get FROZEN at submission (R13) — `vat_eur`/
    `vat_local`/`currency`/`fee_pct`/`fee_min`/`fee_eur` — as nullable columns
    populated by a future submission service, never by this order. `status_code`
    is the two-layer workflow code (1A..5, R17); it stays NULL here — deriving
    it is the checklist/readiness gate's job (out of scope for WO-49).
    """

    __tablename__ = "vat_refund_claims"
    __table_args__ = (
        # R1 — the claim grain. Creating two claims with the same key must
        # upsert, never duplicate; this is the constraint that makes that true
        # even under a lost race (a plain INSERT racing another plain INSERT
        # raises IntegrityError on the loser, exactly like the future
        # `vat_claimed_invoices` lock table, R4).
        UniqueConstraint(
            "org_id",
            "entity_id",
            "refund_country",
            "ref_period",
            name="uq_vat_refund_claims_grain",
        ),
        # Composite FK target for the (future) vat_claim_lines / vat_claimed_
        # invoices tables — the master-context convention (§4.3) for every
        # cross-table reference inside a tenant.
        UniqueConstraint("org_id", "id", name="uq_vat_refund_claims_org_id_id"),
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_refund_claims_entity",
            ondelete="RESTRICT",
        ),
        # Defense-in-depth on the period SHAPE (the service layer is the real
        # validator — app.services.transport.claim.validate_ref_period — but a
        # malformed value must never even reach storage via a raw path).
        CheckConstraint(
            "ref_period LIKE '____-Q_' OR ref_period LIKE '____-YEAR'",
            name="ck_vat_refund_claims_ref_period_shape",
        ),
        Index("ix_vat_refund_claims_org_entity", "org_id", "entity_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The claimant's own legal entity — see module docstring. FK enforced via
    # the composite ForeignKeyConstraint above (org-safe: the entity MUST
    # belong to the same org as the claim).
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    # ISO 3166-1 alpha-2 of the REFUNDING member state (never the claimant's
    # home country by construction — Art. 5 cross-border refund).
    refund_country: Mapped[str] = mapped_column(String(2), nullable=False)
    # "YYYY-Qn" (Q1-Q4) or "YYYY-YEAR" — Art. 16 min 3 months / max 1 year.
    ref_period: Mapped[str] = mapped_column(String(9), nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    status_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    status_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    decision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    action_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    submitted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # Frozen at first entry to a locking state (R13) — a raw period SUM is
    # NEVER a substitute; the (future) freeze computes over exactly the locked
    # claim set. NULL here means "not yet frozen", not "zero".
    vat_eur: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    vat_local: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    fee_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fee_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fee_eur: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)


class VatRefundClaimLine(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A claim line — one row per (invoice, product code), R2. Materializes and
    freezes only at submission (`frozen_at` stamped then); this order ships the
    empty table, not the materialization service.

    `invoice_ref` and `vat_id` are exactly the two fields
    `app.services.transport.claim_gates.is_synthetic()` (R3) inspects — kept as
    their own typed columns (never the Fleet Fuel overloaded `note` column,
    ADR-P3 "what we deliberately do NOT harvest").
    """

    __tablename__ = "vat_claim_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "claim_id"],
            ["vat_refund_claims.org_id", "vat_refund_claims.id"],
            name="fk_vat_claim_lines_claim",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_vat_claim_lines_invoice",
            ondelete="SET NULL",
        ),
        # R11, enforced twice: the (future) goods_codes mapping module is the
        # primary control, but code "9" (luxuries/entertainment) must never
        # reach storage even via a raw path — the historical Fleet Fuel bug
        # this rule exists to prevent. NULL = not yet classified.
        CheckConstraint(
            "goods_code IS NULL OR goods_code <> '9'",
            name="ck_vat_claim_lines_goods_code_never_9",
        ),
        Index("ix_vat_claim_lines_org_claim", "org_id", "claim_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[str] = mapped_column(GUID(), nullable=False)

    # The raw matching reference (Fleet Fuel's "note", split per ADR-P3 —
    # never overloaded with a rebate explanation or a cash-at-pump flag here).
    invoice_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    # VAT id text captured on the line — the second is_synthetic() input.
    vat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The resolved AP invoice, NULL while UNMATCHED/synthetic (R2 — a hard
    # block, never an invented aggregate). Nullable FK to the AP invoice this
    # transport line was captured from (ADR-P3 rule 1).
    invoice_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)

    goods_code: Mapped[str | None] = mapped_column(String(2), nullable=True)  # Art. 9, R11
    product_group: Mapped[str | None] = mapped_column(String(60), nullable=True)

    net_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    vat_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)

    # Added in G2.5 (WO-54) — the refund country's LOCAL-currency amounts,
    # needed so `freeze.freeze_claim_lines` can freeze the claim's "VAT
    # base" in both EUR and local currency (C10) without re-querying
    # `fuel_transactions`. NULL until `build_claim_lines` (G2.4) populates
    # them; additive columns on an already-shipped table, per this
    # codebase's append-only-migration convention (mirrors "R6 — reimbursement
    # batch maker id: additive column only").
    net_local: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    vat_local: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # Stamped at materialization (submission) — NULL means "not yet frozen".
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
