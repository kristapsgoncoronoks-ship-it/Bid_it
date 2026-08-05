"""Receipt control — supplier invoicing cadence + the persisted
cadence × activity control grid (G3.5; `BA_fleet_fuel.md` §3.J, §2.3 —
no dedicated R-number, like G2.5).

WHY TWO TABLES
--------------
§3.J item 1 harvests cadence as a supplier-master column
(`suppliers.invoice_cadence`), but this codebase has NO transport supplier
master — a fuel transaction's `supplier` is a free-text network code
(`fuel_transaction.py`), and the established precedent for per-supplier
transport configuration is WO-61's `supplier_vat_registrations`: a
transport-local tenant table keyed on that same free-text code, never a
column bolted onto the AP `Vendor` (ADR-P3 rule 1 — transport adds no
column to a core table). `VatSupplierCadence` follows that precedent.
`VatReceiptControl` is §3.J item 4's *"results persist in engine-owned
`invoice_receipt_control`"* grid, one row per expectation SLOT.

THE SLOT GRAIN, AND WHY `country` IS `""` (NOT NULL) FOR MOST ROWS
------------------------------------------------------------------
Expectation = cadence × activity (§3.J item 2). A slot is:
- `monthly` — one `M` slot per (entity, supplier, period);
- `semi-monthly` — two slots, `H1` (days 1-15) and `H2` (day 16-end).
  §3.J says only "one invoice per half-month"; the 15/16 split is the
  natural calendar reading (E100's own two invoices per month) — a
  DOCUMENTED INTERPRETATION of an underspecified phrase, like WO-50's
  `line_seq` and WO-52's heuristics;
- `monthly-per-country` — one `M` slot per (entity, supplier, period,
  country) **with activity** (§3.J item 1, verbatim — an inactive country
  is structurally unenumerable and deliberately produces no row).
The natural key is `(org, entity, supplier, period, slot, country)` with
`country=""` for the non-per-country cadences — an empty STRING, never
NULL, because `NULL <> NULL` under a UNIQUE constraint would let duplicate
slots through silently (the exact lesson `fuel_transaction.py`'s natural
key already documents).

THE FOUR STATUSES (§3.J items 2-3, snake_cased to this codebase's
status convention)
-------------------------------------------------------------------
`no_activity` ("NO ACTIVITY — no invoice expected, OK"), `received_doc`
("RECEIVED + DOC": ≥1 transaction in the slot resolves to a registered
invoice AND that invoice has a vaulted document), `received_no_doc`
("RECEIVED no doc"), `missing` ("MISSING — activity but no invoice
registered → chase the supplier").

WHY `waived`/`note` LIVE ON THE COMPUTED ROW, AND HOW THEY SURVIVE
------------------------------------------------------------------
§3.J item 4, verbatim: *"manual overrides (waived / note) survive
re-runs."* `receipt_control.run_receipt_control` upserts each slot's
COMPUTED columns (`status`, `txn_count`) on the natural key and NEVER
writes the override columns; `receipt_control.set_control_override` is
the ONLY writer of `waived`/`note` (grep-proven by
`tests/transport/test_g3_5_receipt_control.py`, the R5 structural-test
pattern). A re-run — including one after a re-ingest changed the
underlying activity — passes an existing row's overrides through
untouched, and rows are never deleted by a re-run.

THIS TABLE IS NOT `vat_receipt_waivers` (WO-58, R15)
----------------------------------------------------
Two different "receipt control" artifacts exist in the harvested spec and
they stay two different tables here. `vat_receipt_waivers` is the
CLAIM-level LEGAL exclusion (grain (org, claim, supplier); refused when
the supplier has registered invoices; excludes the supplier from claim
lines by construction; feeds the blocking checklist item). THIS table is
the period-level OPERATIONAL worklist: `waived` here means "stop chasing
this slot" — a worklist mute with a note, no refusal rule, and ZERO
effect on any claim, line, lock, freeze, checklist or ingest (§4.19
advisory semantics: §3.J's verbs are "answers"/"persist"/"chase", never
"block"/"halt" — the deliberate contrast with R25's two regimes).

Tenant-scoped: both tables listed in `app/core/tenant.py::TENANT_MODELS`,
RLS-policy'd in the SAME migration that creates them (master-context
§4.2).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

# §3.J item 1, verbatim — the closed set of invoicing cadences.
CADENCES = ("semi-monthly", "monthly", "monthly-per-country")
_CADENCE_CHECK = "cadence IN (" + ", ".join(f"'{c}'" for c in CADENCES) + ")"

# §3.J items 2-3 — the cross-control statuses (+ NO ACTIVITY).
RECEIPT_STATUSES = ("no_activity", "received_doc", "received_no_doc", "missing")
_STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in RECEIPT_STATUSES) + ")"

# Slot keys — see the module docstring's slot-grain section.
SLOTS = ("M", "H1", "H2")
_SLOT_CHECK = "slot IN (" + ", ".join(f"'{s}'" for s in SLOTS) + ")"


class VatSupplierCadence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One (org, supplier) → cadence assignment. `receipt_control.
    set_cadence` is the only writer (admin-curated, audited); it BEATS the
    harvested per-network default in `receipt_control.DEFAULT_CADENCES`.
    A supplier with neither resolves to no cadence → no expectations at
    all (opt-in by absence — the R61/WO-66 discipline)."""

    __tablename__ = "vat_supplier_cadences"
    __table_args__ = (
        UniqueConstraint("org_id", "supplier", name="uq_vat_supplier_cadences_key"),
        CheckConstraint(_CADENCE_CHECK, name="ck_vat_supplier_cadences_cadence"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The fuel-card network code (matches FuelTransaction.supplier /
    # Vendor.name — invoice_match.py's "what registered means" note).
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    cadence: Mapped[str] = mapped_column(String(20), nullable=False)


class VatReceiptControl(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One expectation slot of the receipt-control grid — see the module
    docstring for the grain, the statuses, and the override-survival
    contract. `receipt_control.run_receipt_control` upserts the computed
    columns; `receipt_control.set_control_override` is the ONLY writer of
    `waived`/`note`."""

    __tablename__ = "vat_receipt_controls"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "entity_id",
            "supplier",
            "period",
            "slot",
            "country",
            name="uq_vat_receipt_controls_key",
        ),
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_receipt_controls_entity",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_STATUS_CHECK, name="ck_vat_receipt_controls_status"),
        CheckConstraint(_SLOT_CHECK, name="ck_vat_receipt_controls_slot"),
        CheckConstraint("txn_count >= 0", name="ck_vat_receipt_controls_txns_nonneg"),
        Index("ix_vat_receipt_controls_org_period", "org_id", "period"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The claimant entity — same issuer_profiles reuse as FuelTransaction.
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    # "YYYY-MM" — the control month (FuelTransaction.period's own unit).
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    # "M" (monthly / monthly-per-country) | "H1" | "H2" (semi-monthly).
    slot: Mapped[str] = mapped_column(String(2), nullable=False)
    # ISO 3166-1 alpha-2 for monthly-per-country rows; "" otherwise —
    # empty string, never NULL (see module docstring).
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="")

    # Computed on every run — see RECEIPT_STATUSES.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    txn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The manual overrides that SURVIVE re-runs (§3.J item 4) — written
    # ONLY by set_control_override, never by run_receipt_control.
    waived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
