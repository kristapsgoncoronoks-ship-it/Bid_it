"""The recorded OFF-INVOICE rebate document — the only thing allowed to move
`net_eur_eff` away from `net_eur` (G4.2; R50; `BA_fleet_fuel.md` §4.2, §5.1,
§2.5 "Expected rebate").

WHY THIS TABLE EXISTS AT ALL
-----------------------------
`BA_fleet_fuel.md` §4.2's two-tier discount model is a business rule, verbatim:

    "**On-invoice** discounts (TFC hub discount -0.205/L, E100 station-colour
     tiers 0.08-0.23/L, MOEVE PRN off pump PVP, DKV 1.30 SEK/L + 5.63% service
     fee) are already inside `net_eur`.
     **Off-invoice** rebates land ONLY in `net_eur_eff`. **The canonical case is
     Q8/Port One:** Q8 invoices at **LIST price** per country; Port One issues a
     **SEPARATE rebate invoice per country**. The pair must always be
     reconciled. This is the entire reason the `net_eur_eff` column exists."

The second tier arrives on a document the fuel-card statement parser never sees
-- a different document, often from a different legal party. It therefore cannot
come from `statement_ingest`, and no parser can produce it. It has to be
RECORDED. This table is that record, and `app/services/transport/rebate.py` is
its only writer.

THE SOURCE GUARD, HALF ONE: A REBATE MUST BE SOURCED, NEVER INFERRED
----------------------------------------------------------------------
R50 does not only ask for the merge; it asks to *"**Guard the input source** so
a raw file cannot silently replace an adjusted one."* §4.2 names the hazard it
is guarding against:

    "**Hazard:** the Q8 rebate layer depends on `month_config.FILES["Q8"]`
     pointing at the *adjusted* workbook. There is no assertion that it does --
     swapping in the raw file **silently loses the rebate layer**, corrupting
     every price/benchmark/overpay figure."

Fleet Fuel's rebate arrived pre-merged inside a workbook whose provenance
nothing asserted. Here every euro that reaches `net_eur_eff` is traceable to a
row of this table, and a row of this table cannot exist without an IDENTIFIED
source: `source_ref` (the rebate document's own number) and `source_party` (who
issued it -- §5.1 lists the rebate issuer as a party distinct from the fuel
network: *"Q8 / Kuwait Petroleum (+ **Port One** rebate partner)"*). Both are
`CHECK`-constrained non-empty at the DATABASE, not only at the service, for the
`goods_code <> '9'` / `product_group` reason this codebase applies everywhere a
malformed value would become money: the service is the real gate, but a bad
value must never reach storage through a raw path.

The other half of the guard -- "a rebate layer must never silently DISAPPEAR" --
cannot live in a table; it is a question about a period that has NO row here.
`rebate.missing_source_warnings` answers it, learning the expectation from this
table's own history exactly as §2.5's "Expected rebate" row prescribes.

THE GRAIN
----------
`(org, supplier, country, period, source_ref)`. §4.2 fixes the first four:
Port One issues *"a SEPARATE rebate invoice per country"*, per month, against
one fuel network. `source_ref` completes it so a country/period legitimately
covered by TWO rebate documents (a rebate plus a later correction note) is two
rows rather than a silent overwrite -- while re-recording the SAME document
number is recognised as the same document and updates it in place (audited
old->new).

WHY THE FX QUADRUPLE IS HERE, AND WHY THERE IS NO `stated` BRANCH
-------------------------------------------------------------------
A rebate invoice is denominated in a currency like any other document, and the
merge sums EUR only (master-context §4.14 -- never a raw cross-currency sum), so
the conversion has to happen once, here, with its provenance frozen: the same
`fx_rate` / `fx_ecb_rate` / `fx_ecb_date` / `fx_source` quadruple
`fuel_transactions` already carries (`app/models/fx.py`'s platform-wide
convention). EUR is the identity (`fx_source="eur"`); anything else resolves
through `fx.to_eur` at the document's own date and is REFUSED when the cache has
no rate (§4.15: *"`unknown` yields NULL, never a guessed number"* -- and since
`amount_eur` is NOT NULL here, "never a guessed number" means "no row at all").

There is deliberately NO `stated` branch, unlike DKV's statement lines: a rebate
invoice in this model states an AMOUNT, never a rate. Accepting a caller-supplied
rate would be inventing the one number §4.15 exists to forbid.

Tenant-scoped: registered in `app/core/tenant.py::TENANT_MODELS`, RLS-policy'd
in the SAME migration that creates it (master-context §4.2). The composite
`(org_id, id)` unique is the FK target a future per-line allocation table would
use (§4.3's convention); no code references it today.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.fx import FX_SOURCE_CHECK, fx_provenance_check

# The source guard at the storage layer — see the module docstring. An empty
# string is the failure mode these guard against (NULL is already refused by
# nullable=False), because "" is what an unchecked form post produces.
_SOURCE_REF_CHECK = "source_ref <> ''"
_SOURCE_PARTY_CHECK = "source_party <> ''"

# WO-88 — the same FX provenance invariant `fuel_transactions` carries, over
# this table's own EUR column. WO-84 shipped this table with the FX quadruple
# but NO `fx_source` constraint of any kind — not even the value-domain one
# every other FX-bearing table has had since WO-8 (`ck_expense_items_fx_source`,
# `ck_invoices_fx_source`, `ck_fuel_transactions_fx_source`) — so a raw writer
# could store free text, or 'unknown' beside a positive `amount_eur`.
# `rebate._resolve_eur` is already correct (it emits only 'eur'/'ecb' and
# raises `fx_rate_unavailable` otherwise); this is the missing floor beneath it.
#
# WO-89 adds the third conjunct in parity with `fuel_transactions`: a non-EUR
# rebate document claiming the IDENTITY provenance `'eur'` ("the amount was
# already EUR"). `_resolve_eur` cannot produce it — it returns `'eur'` only on
# the `cur == "EUR"` branch — so no service gate is added here (a second check
# would be dead code, WO-88's own reasoning for this table). The constraint is
# the point: storage protects the writers that do not exist yet.
# WO-V: built from the shared predicate rather than copied. See
# `app/models/fx.py::fx_provenance_check`.
_FX_PROVENANCE_CHECK = fx_provenance_check("amount_eur")


class VatOffInvoiceRebate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One recorded off-invoice rebate document for a (supplier, country,
    period). `rebate.record_rebate` is its only writer (audited old→new);
    `rebate.merge_period` reads it and NEVER writes it. Amounts are stored in
    the document currency AND in EUR, the EUR figure resolved once at recording
    with its FX provenance frozen (§4.15)."""

    __tablename__ = "vat_off_invoice_rebates"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "supplier",
            "country",
            "period",
            "source_ref",
            name="uq_vat_off_invoice_rebates_key",
        ),
        UniqueConstraint("org_id", "id", name="uq_vat_off_invoice_rebates_org_id_id"),
        CheckConstraint(_SOURCE_REF_CHECK, name="ck_vat_off_invoice_rebates_source_ref"),
        CheckConstraint(_SOURCE_PARTY_CHECK, name="ck_vat_off_invoice_rebates_source_party"),
        CheckConstraint("amount_local > 0", name="ck_vat_off_invoice_rebates_local_positive"),
        CheckConstraint("amount_eur > 0", name="ck_vat_off_invoice_rebates_eur_positive"),
        CheckConstraint(FX_SOURCE_CHECK, name="ck_vat_off_invoice_rebates_fx_source"),
        CheckConstraint(_FX_PROVENANCE_CHECK, name="ck_vat_off_invoice_rebates_fx_provenance"),
        Index("ix_vat_off_invoice_rebates_lookup", "org_id", "supplier", "period"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The fuel-card network whose invoiced net this rebate adjusts — matches
    # `FuelTransaction.supplier` exactly (admin-curated master data, never
    # fuzzy-matched: the R21 marker-only discipline).
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    # ISO 3166-1 alpha-2 country of SUPPLY — `FuelTransaction.country`. §4.2:
    # "a SEPARATE rebate invoice per country".
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    # "YYYY-MM" — `FuelTransaction.period`.
    period: Mapped[str] = mapped_column(String(7), nullable=False)

    # THE IDENTIFIED SOURCE (see the module docstring). `source_ref` is the
    # rebate document's own number as printed on it; `source_party` is the legal
    # party that issued it, which need not be the fuel network (§5.1's Port One).
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    source_party: Mapped[str] = mapped_column(String(120), nullable=False)

    # The rebate document's own date — the FX as-of date, never "today"
    # (§4.2 row 4's convention: the document's date is what the conversion is
    # audited against).
    rebate_date: Mapped[date] = mapped_column(Date, nullable=False)

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Document currency, q2-quantized by the service.
    amount_local: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # The merged figure. EUR by construction — the only column `merge_period`
    # sums, which is what keeps §4.14 true without a per-call currency check.
    amount_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # FX provenance quadruple — `app/models/fx.py`'s platform-wide convention,
    # identical to `fuel_transactions`. NULL rate columns on an EUR row (the
    # identity conversion involves no rate at all).
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    fx_ecb_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    fx_ecb_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fx_source: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Free-text operator context (which contract clause the rebate settles, the
    # covering e-mail). Never parsed, never a figure.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
