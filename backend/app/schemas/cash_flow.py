from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class CashFlowPointOut(BaseModel):
    period: str
    inflow: Decimal
    outflow: Decimal
    net: Decimal
