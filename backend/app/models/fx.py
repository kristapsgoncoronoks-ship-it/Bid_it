from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EcbRate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single ECB euro foreign-exchange reference rate.

    `rate` is the amount of `currency` per 1 EUR on `rate_date` (the ECB
    convention). EUR itself is implicit (rate 1) and never stored. Rates are
    shared across tenants (reference data), so this table has no org_id.
    """

    __tablename__ = "ecb_rates"
    __table_args__ = (
        UniqueConstraint("rate_date", "currency", name="uq_ecb_rate_date_ccy"),
    )

    rate_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
