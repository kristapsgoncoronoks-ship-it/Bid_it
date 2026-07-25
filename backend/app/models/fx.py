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
