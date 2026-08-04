"""The anti-drift extraction baseline (G3.3 slice 2 / WO-70;
`BA_fleet_fuel.md` §2.1a "Anti-drift", Appendix B "Regression drift:
> 0.02 on net or vat") — the known-good per-(statement × currency)
figures recorded at a statement's FIRST successful registration, against
which every later re-extraction of the SAME bytes is compared.

WHY THE KEY IS THE CONTENT DIGEST, NOT (entity, supplier, period)
--------------------------------------------------------------------
"Re-extraction" means *the same document, possibly different parser
code* — the whole point is to notice a parser CHANGE silently changing
extracted figures. Keying by registration context would compare two
DIFFERENT uploads and manufacture drift out of legitimately different
files. So the identity is `sha256(content)`; `network` and `filename`
are stored for display only, and there is deliberately NO `entity_id`:
the parser never sees the entity (`fuel_card_parser.run(filename,
content)`), so the baseline is a property of (org, bytes) and stays
comparable even if the file is later re-registered under a corrected
entity.

WHY THE FIGURES ARE LOCAL-CURRENCY, PER CURRENCY (interpretations)
--------------------------------------------------------------------
The harvested drift metrics are exactly "net or vat". Two column
choices are documented interpretations (the WO-50 `line_seq` / WO-66
`currency`-grain discipline): (a) LOCAL currency, not EUR — FX
resolution is downstream of extraction and depends on the mutable ECB
rate cache, so an EUR-basis baseline would flag rate-cache changes as
parser drift (false alarms; fail toward not crying wolf); (b) one row
PER CURRENCY — master-context §4.14 forbids summing local amounts
across currencies and WO-64 (Q8) proved one statement can carry
several. `line_count` (exact compare) is a third interpretation: a
parser change that splits or merges lines can drift compensatingly
with equal totals, and a dropped/duplicated line is T-6's worst
failure — recording the count costs one integer.

Money columns are Numeric(14, 2) because the stored figure is a
`q2`-quantized comparison TARGET, never an input to further arithmetic
(the drift compare re-quantizes both sides anyway — §4.9).

`app.services.transport.extraction_baseline.record` is the only
first-time writer (confirm-time, from `statement_ingest`); `rebaseline`
the only overwrite; `remove_baseline` the only deleter — all audited.

Tenant-scoped: the figures derive from a tenant's document, so the
table is org-keyed, listed in `app/core/tenant.py::TENANT_MODELS`, and
RLS-policy'd in the SAME migration that creates it (§4.1/§4.2).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class FuelExtractionBaseline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One currency's known-good extraction aggregate for one statement
    document — see the module docstring for the grain and which columns
    are harvested vs interpreted."""

    __tablename__ = "fuel_extraction_baselines"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "statement_sha256",
            "currency",
            name="uq_fuel_extraction_baselines_key",
        ),
        CheckConstraint("line_count >= 0", name="ck_fuel_extraction_baselines_lines_nonneg"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The statement bytes' identity — hex SHA-256 (64 chars).
    statement_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 4217
    # Display-only provenance (never part of the comparison key).
    network: Mapped[str] = mapped_column(String(60), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # The known-good figures: exact line count (interpretation), and the
    # harvested net/vat totals (q2 of the Decimal sum of the parsed
    # local-currency figures).
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    net_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    vat_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
