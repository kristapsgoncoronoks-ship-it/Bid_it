"""The org's own thresholds for the supplier-reliability bands (WO-Q).

WHY THIS TABLE EXISTS, AND WHY IT HOLDS ONLY THRESHOLDS
--------------------------------------------------------
`docs/design/supplier-reliability-rating.md` settles the shape: the rating
itself is **derived** — every criterion is computed on read from rows that
already exist (`vat_overcharge_claims`, `fuel_transactions.fx_source` and its
two rates, `contract_audit`'s findings), so no rating, band or score is ever
stored and none can go stale. The ONE thing a derivation cannot reconstruct is
the org's own tolerance: at what point does a pattern stop being noise and
start being a finding.

That is what this table holds, and nothing else. Three numbers. An org with no
row here behaves exactly as `reliability.DEFAULT_THRESHOLDS` describes, so this
table needs no backfill and no seeded row.

The design's own words on why the numbers must be visible rather than tuned in
secret: the band label is rendered *"next to the rule that produced it"*, so a
reader always sees both "recurring" and "≥ 3 cases in 12 months". A hidden
threshold would turn evidence back into a verdict.

WHY A TRANSPORT-LOCAL TABLE (the same reasoning `vat_excise_rates` records)
----------------------------------------------------------------------------
This codebase has no per-org key/value settings table — `app/models/` contains
no settings model at all — so there is no existing mechanism to reuse and none
is invented. The established precedent for admin-curated transport
configuration is a transport-local tenant table with an audited `set_*` verb:
`vat_excise_rates` (WO-91), `vat_fee_rates` (WO-95),
`supplier_vat_registrations` (WO-61). This is that pattern, once more.

NEW TENANT table — RLS ships in the same migration (`c4e6a8b0d2f5`).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class VatReliabilityThreshold(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per org — the point at which each criterion's band turns.

    Every column is the RECURRING boundary: at or above it the criterion reads
    `recurring`, below it (but above zero findings) `findings`, and with no
    findings at all `clean`. Storing the upper boundary rather than a pair
    keeps the three-value band arithmetic in one place and makes an
    accidentally-inverted range unrepresentable.
    """

    __tablename__ = "vat_reliability_thresholds"
    __table_args__ = (
        UniqueConstraint("org_id", name="uq_vat_reliability_thresholds_org"),
        UniqueConstraint("org_id", "id", name="uq_vat_reliability_thresholds_org_id_id"),
        CheckConstraint(
            "overcharge_cases >= 1", name="ck_vat_reliability_thresholds_cases_positive"
        ),
        CheckConstraint(
            "overcharge_eur_per_1000 > 0", name="ck_vat_reliability_thresholds_eur_positive"
        ),
        CheckConstraint("fx_markup_bps > 0", name="ck_vat_reliability_thresholds_bps_positive"),
        CheckConstraint(
            "ungoverned_share_pct > 0 AND ungoverned_share_pct <= 100",
            name="ck_vat_reliability_thresholds_share_range",
        ),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Overcharge criterion, first half — cases in the window at or above this
    #: count read `recurring`. A count, not a euro: three separate breaches is a
    #: pattern whatever they add up to.
    overcharge_cases: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Overcharge criterion, second half — detected euros per €1,000 of net
    #: spend with this supplier. Normalised, because a big supplier will always
    #: carry more absolute euros than a small one and the rating must not
    #: mistake size for unreliability.
    overcharge_eur_per_1000: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    #: FX criterion — the median markup of the supplier's OWN stated rate over
    #: the ECB rate for the same day, in basis points.
    fx_markup_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Never-agreed criterion — the share (%) of the supplier's validated lines
    #: that no contract term governs, or that breached one.
    ungoverned_share_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
