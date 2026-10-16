"""Read-only summaries of saved received-line calculations, not tax advice."""

from decimal import Decimal

from pydantic import BaseModel


class ReceivedVatRate(BaseModel):
    tax_rate: Decimal
    net: Decimal
    tax: Decimal | None
    gross: Decimal | None
    line_count: int


class ReceivedVatBreakdown(BaseModel):
    rates: list[ReceivedVatRate]
    complete: bool
    matches_header: bool | None
