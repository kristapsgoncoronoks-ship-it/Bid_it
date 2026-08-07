"""The supplier overcharge CLAIM-BACK (G4.5; R41; `BA_fleet_fuel.md` §2.4,
§4.5).

WHAT THIS TABLE IS
------------------
R41, verbatim: *"**Supplier overcharge claim-back**: a per-(supplier × period)
lifecycle `detected → packaged → claimed → recovered | rejected | written_off`
… and a `recovered_total` that feeds the north star. Read-only over the
analytics."* §4.5's lifecycle table gives the same six states. §2.4 frames the
surface: *"What does the supplier owe us for breaching the contract?"* — and its
legal-framing table is emphatic that this analysis alone is **"Money the
supplier owes"**, backed by a claim letter with a 30-day demand, in deliberate
contrast to the same-day overpay figure which is *"negotiation evidence, NOT a
contractual claim-back"* (R53). The framing is not decoration: it is why this
row exists as a tracked receivable-shaped worklist item and the overpay figure
does not.

THE GRAIN — `(org, supplier, period)`
--------------------------------------
R41 says "per-(supplier × period)" without naming which period vocabulary. It is
`FuelTransaction.period` (`"YYYY-MM"`, the accounting month) — the grain
detection runs at and the only one the evidence lines of a single claim share. A
quarter is three claim-backs, which is also how a supplier is actually chased. A
DOCUMENTED INTERPRETATION (see `docs/plan/plan-a/wo/WO-82-overcharge-claimback.md`).

Deliberately NOT keyed on the claimant entity: §4.4's harvested term table
carries no customer dimension and R41's grain names only the supplier and the
period. One letter goes to one supplier for one month, covering every entity's
lines in it.

THE SIX STATES, AND WHY THE CHAIN STAYS LINEAR
-----------------------------------------------
`OVERCHARGE_STATES` is the harvested six, and `overcharge.TRANSITIONS` is the
edge set drawn exactly as §4.5 draws it: `detected → packaged → claimed →
{recovered, rejected, written_off}`. No shortcut edge (e.g. writing a claim off
straight from `detected`) is invented — the WO-73 precedent, where `inactive` is
terminal because no re-onboarding edge was harvested. The three outcome states
are terminal.

MONEY: TWO FIGURES, TWO MEANINGS, BOTH EUR
-------------------------------------------
`detected_eur` is the SNAPSHOT of what `contract_audit.audit()` found when the
claim-back was opened — the euro the demand letter quotes. It is frozen at
`open_claim` and never re-derived, for the same reason a VAT claim's base is
frozen at submission (G2.5/ADR-P3): the figure an operator sent a supplier must
not move underneath them when a later ingest changes a line.

`recovered_eur` is BOOKED CASH — what the supplier actually credited or refunded
— and is written only by the `recovered` transition. §2.4 is explicit that this
is the north-star figure: *"`recovered_total()` = the booked-cash north star."*
It is bounded by `detected_eur` (master-context §4 invariant 13, no
over-crediting, applied to this ledger: a north-star total unbounded by its own
evidence is not a measurement).

Both are EUR by construction, never a currency-ambiguous figure: they derive
from `FuelTransaction.net_eur`/`net_eur_eff`, the EUR columns (§4.14 — see
`contract_audit.py`'s docstring for the full argument, including why the
document-currency columns are never read).

Tenant-scoped: registered in `app/core/tenant.py::TENANT_MODELS`, RLS-policy'd
in the SAME migration that creates it (master-context §4.2). The composite
`(org_id, id)` unique is the FK target a future evidence/letter artifact table
would use (§4.3's convention); no code references it today.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# `BA_fleet_fuel.md` §4.5 / R41, verbatim (snake_cased where the spec hyphenates
# nothing — `written_off` is the spec's own spelling).
OVERCHARGE_STATES = (
    "detected",
    "packaged",
    "claimed",
    "recovered",
    "rejected",
    "written_off",
)
_STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in OVERCHARGE_STATES) + ")"


class VatOverchargeClaim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One (org, supplier, period) claim-back. `overcharge.open_claim` creates
    it at `detected`; `overcharge.advance_claim` is the ONLY function that moves
    its `status` or writes `recovered_eur` (every real transition audited
    old→new, §4.16). Detection itself writes nothing here — it is read-only over
    `fuel_transactions` (R41: "read-only over the analytics")."""

    __tablename__ = "vat_overcharge_claims"
    __table_args__ = (
        UniqueConstraint("org_id", "supplier", "period", name="uq_vat_overcharge_claims_key"),
        UniqueConstraint("org_id", "id", name="uq_vat_overcharge_claims_org_id_id"),
        CheckConstraint(_STATUS_CHECK, name="ck_vat_overcharge_claims_status"),
        CheckConstraint("detected_eur > 0", name="ck_vat_overcharge_claims_detected_positive"),
        CheckConstraint(
            "recovered_eur IS NULL OR recovered_eur > 0",
            name="ck_vat_overcharge_claims_recovered_positive",
        ),
        CheckConstraint("lines_count >= 0", name="ck_vat_overcharge_claims_lines_nonneg"),
        Index("ix_vat_overcharge_claims_org_period", "org_id", "period"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The fuel-card network code — `FuelTransaction.supplier`.
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    # "YYYY-MM" — `FuelTransaction.period` (see the module docstring's grain).
    period: Mapped[str] = mapped_column(String(7), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="detected")

    # The frozen euro the demand letter quotes — see the module docstring.
    detected_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # How many breach findings that euro is made of (the artifact slice renders
    # exactly this many evidence rows from the same source).
    lines_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Booked cash — written only by the `recovered` transition, bounded by
    # `detected_eur`. NULL until then, never 0 (a 0 would read as "the supplier
    # paid nothing back", which is what `rejected`/`written_off` say).
    recovered_eur: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # Free-text operator context carried on a transition (a credit-note number,
    # the supplier's stated reason for rejecting). Never parsed, never a figure.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
