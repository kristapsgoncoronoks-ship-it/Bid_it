"""The admin override of a state's diesel-excise refund rate (G4.6; R42;
`BA_fleet_fuel.md` §2.4 `/excise`, §3.L, Appendix B "EXCISE").

WHAT THIS TABLE IS — AND WHAT IT DELIBERATELY IS NOT
------------------------------------------------------
`BA_fleet_fuel.md` §2.4's `/excise` row fixes the mechanism and the default in
one sentence, verbatim:

    "7 countries (BE·FR·IT·SI·HU·ES·HR); `litres × rate/1,000L`; rate default
     **EUR 30/1,000 L is an explicit PLACEHOLDER** in the reported EUR 25-33
     band, admin-overridable. **Asserts NO eligibility.** Separate regime,
     filed with **CUSTOMS**."

Appendix B repeats it as a constant (*"Default rate EUR 30.00 / 1,000 L
(PLACEHOLDER in the reported EUR 25-33 band)"*), and R42 states the requirement:
*"Rates are admin-overridable and the figure asserts NO eligibility — surfaced
loudly."*

So the DEFAULT is a code constant (`excise.DEFAULT_RATE_EUR_PER_1000L`) and this
table is only the OVERRIDE. An org with no row here behaves exactly as the
harvested placeholder describes; a row here means an operator has typed the rate
their customs authority actually applies. That split matters: §9.2 item 13 files
*"who owns the real per-country statutory rates, and how often do they change
(quarterly, per the research)?"* as an OPEN owner question, so the platform must
never present its own default as a statutory figure, and must let a human
replace it the moment they have the real one.

**This table asserts nothing about eligibility.** §3.L, verbatim: *"`excise.py`
| **Asserts NO eligibility** (vehicle >=7.5t / carrier registration not
modelled); rates are indicative defaults."* A rate is what a state pays per
1,000 litres to a haulier that QUALIFIES; whether this haulier qualifies is not
modelled anywhere in this product and is not stored here.

WHY A TRANSPORT-LOCAL REGISTRY, AND WHY NOT A GENERIC SETTINGS ROW
--------------------------------------------------------------------
This codebase has no per-org key/value settings table — `app/models/` contains
no settings model at all — so there is no "existing settings mechanism" to
reuse and none is invented (master-context §10). The established precedent for
admin-curated transport configuration is a transport-local tenant table with
audited `set_*`/`remove_*` verbs: `supplier_vat_registrations` (WO-61),
`vat_supplier_cadences` (WO-72) and `vat_supplier_contract_terms` (WO-82). This
follows that precedent exactly, including ADR-P3 rule 1 (never a column bolted
onto another domain's table).

THE GRAIN IS `(org, country)` — ONE RATE PER STATE
----------------------------------------------------
The harvested mechanism has exactly one rate dimension: the country. §2.4 says
`litres × rate/1,000L` per country and R42 says the analysis grain is
per (entity × country) — the ENTITY is a grain of the litres, never of the rate,
because the rate is the state's, not the haulier's. No product/period/entity
dimension is added: a term this model cannot express is a term this product does
not apply, stated rather than approximated (the `contract_term.py` discipline).

`country` is CHECK-constrained non-empty at the database and gated in the
service against the harvested seven-country set — see `excise.set_rate` for why
that gate is fail-CLOSED and what it deliberately costs.

PRECISION: `Numeric(12, 4)`, NOT `Numeric(14, 2)`
--------------------------------------------------
This is a RATE, not an amount — `contract_term.py`'s own reasoning, applied to a
different denominator. Four decimal places on a EUR-per-1,000-litre figure carry
the harvested EUR 30.00 exactly with room for a real statutory rate that is not
a round euro (the EUR 25-33 band §2.4 reports is a research band, not a set of
integers). Money AMOUNTS elsewhere stay `Numeric(14, 2)`; nothing in this table
is an amount, and the euro the analysis reports is quantized once, in the
service, from the exact litre total.

A `CHECK (rate_eur_per_1000l > 0)` refuses a nil or negative rate at the
database level — the defense-in-depth discipline of `goods_code <> '9'` and the
`product_group` CHECK. A nil rate would be the ABSENCE of the regime, which the
seven-country membership already expresses; storing it as a zero would make the
analysis report a EUR 0.00 finding, and *"this state refunds you nothing"* is a
materially different (and false) claim from *"we hold no rate for this state"*.

Tenant-scoped: registered in `app/core/tenant.py::TENANT_MODELS`, RLS-policy'd
in the SAME migration that creates it (master-context §4.2), with the composite
`(org_id, id)` unique §4.3 requires of every tenant table so a future row can FK
to a rate. No code references that composite today.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# A rate must be a real rate — see the module docstring on why a zero is refused
# rather than stored as "no refund".
_RATE_POSITIVE_CHECK = "rate_eur_per_1000l > 0"
_COUNTRY_PRESENT_CHECK = "country <> ''"


class VatExciseRate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One state's diesel-excise refund rate, in EUR per 1,000 litres, as an
    operator typed it. `excise.set_rate` / `excise.remove_rate` are its only
    writers (audited old->new); `excise.excise_report` reads it and NEVER
    writes. Absence of a row means the harvested indicative default applies.

    The figure this rate produces **asserts no eligibility** (`BA_fleet_fuel.md`
    §3.L) — vehicle weight and carrier registration are not modelled here or
    anywhere else in this product.
    """

    __tablename__ = "vat_excise_rates"
    __table_args__ = (
        UniqueConstraint("org_id", "country", name="uq_vat_excise_rates_key"),
        UniqueConstraint("org_id", "id", name="uq_vat_excise_rates_org_id_id"),
        CheckConstraint(_RATE_POSITIVE_CHECK, name="ck_vat_excise_rates_rate_positive"),
        CheckConstraint(_COUNTRY_PRESENT_CHECK, name="ck_vat_excise_rates_country_present"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ISO 3166-1 alpha-2 country of SUPPLY — `FuelTransaction.country`. The
    # service restricts writes to the harvested seven states that operate the
    # regime; the database only refuses an empty string.
    country: Mapped[str] = mapped_column(String(2), nullable=False)

    # EUR per 1,000 litres. A RATE, not an amount — see the module docstring for
    # why the scale is 4 and why a non-positive value cannot be stored.
    rate_eur_per_1000l: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
