"""The configured contingency-fee rate (G2.9; R13; `BA_fleet_fuel.md` C10/C11,
R40; the owner decision recorded in `docs/DECISIONS-NEEDED.md` "Decisions taken —
2026-08-08").

WHAT THIS TABLE HOLDS, AND WHAT IT DELIBERATELY DOES NOT
----------------------------------------------------------
The transport vertical is sold on a **contingency fee on recovered VAT** — the
owner's 2026-08-08 decision, and `BA_fleet_fuel.md` line 41's own framing
(*"charges a **contingency fee**"*). §2.2 states the arithmetic in one line:

    fee = max(fee_pct% × base, fee_min)   [customer_master.compute_fee]
    rate FROZEN at submission; fee CHARGED on the PAID amount

This table is where `fee_pct` and `fee_min` are CONFIGURED before a claim is
filed. It is not where a fee is stored: the moment a claim is submitted, C10
freezes the rate onto the claim itself (`vat_refund_claims.fee_pct/fee_min/
fee_eur`), and from then on the claim carries its own copy and never reads this
table again. Editing a row here changes what FUTURE submissions freeze and
nothing else — R13's acceptance line, verbatim: *"Change the customer fee %
after submission ⇒ the claim's fee is unchanged."*

**There is no default rate anywhere.** An org with no row here cannot submit a
claim; `app.services.transport.fee.resolve_fee_rate` refuses with
`fee_rate_not_configured`. That is the whole point of the table and is argued in
that module's docstring rather than repeated here.

THE THREE RUNGS, AND WHY THEY ARE ONE TABLE
----------------------------------------------
C11 fixes the resolution order verbatim: *"Resolution order for the rate
(`fee_for`): per-(customer, country) override → customer default → (0, 0)."*
R40 names the rung above it: *"Standard fee is admin-editable; a per-client fee
overrides it."* The owner's decision names the same thing — *"the rate as an
org-level setting"*. So three rungs, most specific first:

    (entity, country)  →  (entity, '')  →  (NULL, '')  →  REFUSE

`entity_id` is the CUSTOMER. That identity is not invented here: WO-73 (G2.11,
R44) shipped `VatCustomerLifecycle`, a table keyed `(org, entity_id)` and named
for the customer, and `lock.submit_claim` already refuses any claim whose entity
is not an `active` customer with the refund country separately activated. The
claimant entity IS the billable client in this codebase, decided and shipped.

One table rather than three because the three rungs are one FACT — a
`(fee_pct, fee_min)` pair — read by one resolution walk. Splitting them would
put the same two columns in three places and let them drift.

WHY THE UNIQUENESS IS A CONSTRAINT PLUS A PARTIAL INDEX
---------------------------------------------------------
`entity_id` is NULL on the org-standard rung, and SQL treats NULLs as DISTINCT
in a UNIQUE constraint — so a plain `UNIQUE(org_id, entity_id, country)` would
happily store two org standards, and the resolution walk would then have to pick
one arbitrarily. It does not have to: a **partial unique index on `(org_id)
WHERE entity_id IS NULL`** makes "at most one standard rate per org" a database
fact, and partial indexes behave identically on SQLite and PostgreSQL. The plain
UNIQUE still covers the two customer rungs, where both key columns are non-NULL.

`country` is therefore NOT nullable: the empty string is the sentinel for *"this
rate applies to every refund country"*, which is the customer-default rung. It
is a sentinel and never a country — `country = '' OR length(country) = 2` is
CHECK-enforced, and the service upper-cases and validates a real code. Using
`''` here rather than a second NULL is what keeps the customer rungs on one
ordinary UNIQUE constraint instead of a second partial index.

`entity_id IS NOT NULL OR country = ''` is CHECK-enforced too: **no org-level
per-country rung is harvested.** Neither C11 nor R40 describes one, and a shape
the specification does not describe is not stored (the `contract_term.py`
discipline — *"a term this model cannot express is a term this product does not
apply, stated rather than approximated"*).

WHY A ZERO RATE IS STORABLE, AND WHY THAT IS NOT A CONTRADICTION
-------------------------------------------------------------------
`fee_pct >= 0` and `fee_min >= 0` — a row of `(0, 0)` is legal. That looks like
the very outcome the fail-closed resolution exists to prevent, and it is the
opposite of it. The refusal is about **absence**: nobody has decided, so nothing
may be charged and nothing may be filed under an unknown rate. A row of
`(0, 0)` is a **decision** — a human typed it, `fee.set_rate` audited it with an
actor and an old→new pair, and "this client pays no success fee" is a real
commercial arrangement (the platform already sells the transport module at
€0/month, `BA_fleet_fuel.md` line 125, precisely because the fee is where the
money is; a bundled or waived fee is a variant of that, not an error).

`fee_pct <= 100` is the upper CHECK. A contingency fee above the whole recovery
would hand the client a bill larger than the refund; that is not a commercial
arrangement this product supports, and the database says so rather than trusting
a typo not to happen.

PRECISION — THE CLAIM'S OWN COLUMNS, EXACTLY
-----------------------------------------------
`fee_pct` is `Numeric(5, 2)` and `fee_min` is `Numeric(14, 2)`, matching
`VatRefundClaim.fee_pct`/`fee_min` byte for byte. The freeze is then a straight
copy with no requantisation, so the rate a claim was filed under is provably the
rate that was configured. `fee_min` IS a money amount (unlike
`vat_excise_rates.rate_eur_per_1000l`, which is a rate and carries scale 4), so
it takes the codebase's standard money scale.

Tenant-scoped: registered in `app/core/tenant.py::TENANT_MODELS`, RLS-policy'd in
the SAME migration that creates it (master-context §4.2), with the composite
`(org_id, id)` unique §4.3 requires of every tenant table.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# The sentinel `country` value meaning "every refund country" — the customer-
# default rung of C11's chain, and the only value the org-standard rung may
# carry. Never a country; see the module docstring.
ANY_COUNTRY = ""

# Defense-in-depth behind `app.services.transport.fee`'s own validation — the
# `excise_rate.py` discipline (the service is the real validator; a malformed
# value must never reach storage even via a raw path).
_PCT_RANGE_CHECK = "fee_pct >= 0 AND fee_pct <= 100"
_MIN_NON_NEGATIVE_CHECK = "fee_min >= 0"
_COUNTRY_SHAPE_CHECK = "country = '' OR length(country) = 2"
# No org-level per-country rung is harvested (C11 / R40) — see the docstring.
_ORG_RUNG_HAS_NO_COUNTRY_CHECK = "entity_id IS NOT NULL OR country = ''"


class VatFeeRate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One configured `(fee_pct, fee_min)` pair, at one of C11's three rungs.

    `app.services.transport.fee.set_rate` / `remove_rate` are its only writers
    (audited old→new); `resolve_fee_rate` is its only reader, and the claim
    freeze copies the resolved values onto the claim rather than referencing
    this row — so deleting a rate never changes a filed claim's fee.
    """

    __tablename__ = "vat_fee_rates"
    __table_args__ = (
        # The two CUSTOMER rungs — both key columns non-NULL, so an ordinary
        # UNIQUE is exact here.
        UniqueConstraint("org_id", "entity_id", "country", name="uq_vat_fee_rates_key"),
        UniqueConstraint("org_id", "id", name="uq_vat_fee_rates_org_id_id"),
        # The ORG-STANDARD rung — at most one per org. A plain UNIQUE cannot
        # express this because SQL treats NULLs as distinct; see the docstring.
        Index(
            "uq_vat_fee_rates_org_standard",
            "org_id",
            unique=True,
            sqlite_where=text("entity_id IS NULL"),
            postgresql_where=text("entity_id IS NULL"),
        ),
        # The customer is the claimant entity (WO-73) — org-safe composite FK,
        # satisfied trivially when `entity_id` is NULL (MATCH SIMPLE).
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_fee_rates_entity",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_PCT_RANGE_CHECK, name="ck_vat_fee_rates_pct_range"),
        CheckConstraint(_MIN_NON_NEGATIVE_CHECK, name="ck_vat_fee_rates_min_non_negative"),
        CheckConstraint(_COUNTRY_SHAPE_CHECK, name="ck_vat_fee_rates_country_shape"),
        CheckConstraint(
            _ORG_RUNG_HAS_NO_COUNTRY_CHECK, name="ck_vat_fee_rates_org_rung_has_no_country"
        ),
        Index("ix_vat_fee_rates_org_entity", "org_id", "entity_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The CUSTOMER — the claimant entity (`issuer_profiles`), the identity WO-73
    # established. NULL = the org-level STANDARD rate (R40 / the 2026-08-08
    # decision), the last rung before the refusal.
    entity_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    # ISO 3166-1 alpha-2 refund country, or `ANY_COUNTRY` ('') for a rate that
    # applies to every country. Never NULL — see the docstring.
    country: Mapped[str] = mapped_column(String(2), nullable=False, default=ANY_COUNTRY)

    # Percentage of the frozen VAT base. Scale matches VatRefundClaim.fee_pct.
    fee_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    # Per-declaration minimum, NET EUR. Scale matches VatRefundClaim.fee_min.
    fee_min: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
