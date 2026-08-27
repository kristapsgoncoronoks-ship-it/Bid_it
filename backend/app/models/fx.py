from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FxSource(str, enum.Enum):
    """How a stored EUR figure was derived — the FX provenance enum (ADR-0010).

    One convention everywhere: ECB reference rates are units of the foreign
    currency per 1 EUR, so converting TO EUR divides. `unknown` means no
    conversion was possible — the EUR figure is NULL, never a guessed number.
    """

    eur = "eur"  # the amount was already EUR (identity, rate 1)
    stated = "stated"  # the document / claimant stated the conversion
    ecb = "ecb"  # converted at the cached ECB reference rate
    unknown = "unknown"  # no rate available → EUR figure is NULL


FX_SOURCES: tuple[str, ...] = tuple(m.value for m in FxSource)
# SQL fragment shared by the model CHECK constraints and the migration.
FX_SOURCE_CHECK = "fx_source IS NULL OR fx_source IN ('eur', 'stated', 'ecb', 'unknown')"


def fx_provenance_check(
    eur_column: str, currency_column: str = "currency", *, eur_nullable: bool = False
) -> str:
    """THE TRIPLE GUARD (WO-88 + WO-89), as one SQL fragment any table can adopt.

    `FX_SOURCE_CHECK` above is a VALUE-DOMAIN check: it says `fx_source` holds
    one of four words. It says nothing about whether that word is TRUE of the
    row it sits on — and three combinations are lies a database will happily
    store:

    1. **`unknown` beside a euro figure.** `unknown` means no rate was
       available, so the euro must be NULL. A row asserting both says "we could
       not convert this, and here is the conversion."
    2. **A non-EUR document with no provenance at all.** A converted amount is
       meaningless without the rate that produced it (§4.15) — a NULL
       `fx_source` on a foreign-currency row is a number nobody can audit.
    3. **A non-EUR document claiming `eur` provenance.** `eur` is the IDENTITY
       provenance: "the amount was already EUR, rate 1, no conversion required."
       On a PLN row that is a fabricated conversion wearing the one label that
       makes anyone check it. WO-89 found exactly this reaching claim lines, a
       demand letter, the tie-out and the recovery dashboard unchallenged.

    WHY THIS IS A FUNCTION AND NOT A CONSTANT. The predicate is identical
    everywhere; only the name of the euro column changes (`net_eur`,
    `amount_eur`, `total_eur`). It lived as a hand-copied literal on two
    transport tables until WO-V, which is precisely how the third and fourth
    tables end up with a subtly different rule — the failure mode WO-85's query
    registry exists to prevent one layer down.

    `eur_nullable` IS NOT A STYLE FLAG — IT CHANGES WHAT CLAUSE 2 MEANS.
    Clause 2 refuses "a foreign document with no provenance". On WO-88/89's two
    tables the euro column is NOT NULL, so every row HAS a converted amount and
    the clause reads correctly as written. On a table where the euro is
    NULLABLE, a foreign row with `total_eur` NULL and `fx_source` NULL is not
    dishonest — it is simply **not converted yet**, and nothing is being
    claimed. Applying the unguarded clause there refuses a legitimate row,
    which is a worse defect than the one the guard fixes. So when the euro can
    be NULL, clause 2 only bites once there is a euro to explain.

    (Clause 3 stays unconditional on purpose: a non-EUR row claiming the `eur`
    identity is asserting "no conversion was required", and that is a false
    statement about the CURRENCY whether or not a figure has been computed yet.)

    A table can only adopt this if it HAS the columns to be honest with: a
    currency, a provenance and a euro. `expense_items` and `expense_reports` do
    not (see `docs/design/fx-provenance-coverage.md`), and a constraint copied
    onto them would be theatre.
    """
    # Clause ORDER is load-bearing, not stylistic: the two transport tables
    # already carry this predicate as stored SQL, so re-ordering the disjuncts
    # here (logically identical, textually different) would read as schema drift
    # to `alembic check` and demand a migration that changes nothing. For the
    # same reason the `eur_nullable` disjunct is APPENDED to clause 2 rather
    # than folded in — with `eur_nullable=False` the string is byte-identical to
    # what those tables already store.
    unconverted = f"{eur_column} IS NULL OR " if eur_nullable else ""
    return (
        f"(fx_source IS NULL OR fx_source <> 'unknown' OR {eur_column} IS NULL)"
        f" AND ({unconverted}upper({currency_column}) = 'EUR' OR fx_source IS NOT NULL)"
        f" AND (upper({currency_column}) = 'EUR' OR fx_source <> 'eur')"
    )


class EcbRate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single ECB euro foreign-exchange reference rate.

    `rate` is the amount of `currency` per 1 EUR on `rate_date` (the ECB
    convention). EUR itself is implicit (rate 1) and never stored. Rates are
    shared across tenants (reference data), so this table has no org_id.
    """

    __tablename__ = "ecb_rates"
    __table_args__ = (UniqueConstraint("rate_date", "currency", name="uq_ecb_rate_date_ccy"),)

    rate_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
