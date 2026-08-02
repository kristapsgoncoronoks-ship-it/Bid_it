"""The engine tie-out expectation (G3.3 / WO-66; `BA_fleet_fuel.md` §2.1a,
§5.1, R25) — the per-(entity, supplier, period, currency) figures a HUMAN
TYPES FROM THE INVOICE PDF, against which the monthly close ties out the
period's captured `fuel_transactions`.

WHY THIS IS A TABLE A HUMAN MAINTAINS, NOT A DERIVED ARTIFACT
----------------------------------------------------------------
The whole point of the tie-out is INDEPENDENCE from the capture pipeline:
"does what we parsed equal what the invoice PDF says?" only means
something when the right-hand side comes from OUTSIDE the parser — the
figures printed on the supplier's own document, keyed in by an operator
(the harvested §5.1 onboarding contract: "fill `expected` **from the
invoice** -> run the tie-out: PASS = trained"; spec open question 16
confirms `expected` is human-maintained monthly, and names that as the
real operating cost of every supplier — a cost this product accepts
because T-6 rates parser accuracy the moat-level risk).

WHY THE GRAIN CARRIES `currency` (a documented interpretation)
----------------------------------------------------------------
Fleet Fuel's `expected` was keyed per supplier over single-currency
statement files. This codebase proved (WO-64, Q8) that one statement can
legitimately carry lines in multiple currencies, and master-context §4.14
forbids summing `gross_local` across currencies — so the expectation
gains a `currency` column: each typed row ties out the period's lines OF
THAT CURRENCY for that (entity, supplier). A single-currency supplier
types exactly one row per period, byte-equivalent to the harvested
behaviour. Flagged as an interpretation the same way WO-50 flagged
`line_seq`.

METRICS AND TOLERANCES (harvested §2.1a / Appendix B "VALIDATION")
--------------------------------------------------------------------
`expected_lines` — REQUIRED, tolerance 0, EXACT, always (a line-count
mismatch is a dropped or duplicated transaction — never tolerable).
`expected_gross_local` — optional; per-row `gross_local_tolerance`
CHECK-bounded to the harvested 0.02–0.05 range (Fleet Fuel varied it per
supplier), default 0.02 (the strict end — fail toward catching drift).
`expected_net_eur` / `expected_gross_eur` — optional, tolerance 0.05
(fixed, harvested). `expected_diesel_litres` — optional; the source
names the metric but no tolerance, so the 0.05 figure is REUSED and
documented as an interpretation (litres basis, 3 dp). A NULL metric is
simply not checked — only figures actually typed from the PDF tie out.

Tenant-scoped: composite `(org_id, entity_id)` FK into `issuer_profiles`
(master-context §4.3), listed in `app/core/tenant.py::TENANT_MODELS`,
RLS-policy'd in the SAME migration that creates the table (§4.2).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class FuelTieOutExpectation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One typed tie-out target — see the module docstring for the grain,
    the metrics and which tolerances are harvested vs interpreted.
    `app.services.transport.tie_out.set_expectation` is the only writer
    (upsert by the natural key, audited old->new)."""

    __tablename__ = "fuel_tieout_expectations"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "entity_id",
            "supplier",
            "period",
            "currency",
            name="uq_fuel_tieout_expectations_key",
        ),
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_fuel_tieout_expectations_entity",
            ondelete="RESTRICT",
        ),
        CheckConstraint("expected_lines >= 0", name="ck_fuel_tieout_expectations_lines_nonneg"),
        # The harvested 0.02–0.05 tolerance band — defense-in-depth against
        # a raw write widening the tie-out beyond what the source allowed.
        CheckConstraint(
            "gross_local_tolerance >= 0.02 AND gross_local_tolerance <= 0.05",
            name="ck_fuel_tieout_expectations_gross_tol",
        ),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The claimant's own legal entity — same issuer_profiles reuse as
    # fuel_transactions.entity_id (FK via the composite constraint above).
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "YYYY-MM"
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 4217

    # Tolerance 0 — EXACT, always (harvested).
    expected_lines: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_gross_local: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    gross_local_tolerance: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("0.02")
    )
    expected_net_eur: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    expected_gross_eur: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Litres (3 dp, the qty basis) — NOT money; never q2'd.
    expected_diesel_litres: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
