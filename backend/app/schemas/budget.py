from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BudgetTargetIn(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    monthly_limit: Decimal = Field(ge=0)


class BudgetTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    category: str
    monthly_limit: Decimal


class BudgetRow(BaseModel):
    category: str
    budget: Decimal
    actual: Decimal
    remaining: Decimal
    pct: int | None  # actual as % of budget (None when no budget set)
    over: bool
    untargeted: bool  # spend in a category with no budget target


class BudgetTrendPoint(BaseModel):
    month: str
    actual: Decimal
    budget: Decimal


class BudgetOverview(BaseModel):
    month: str
    currency: str
    total_budget: Decimal
    total_actual: Decimal
    total_remaining: Decimal
    over_budget: bool
    rows: list[BudgetRow]
    trend: list[BudgetTrendPoint]
    # C1.7/WO-24: invoices in `month` that could not be converted to EUR (no
    # rate ever resolved) and were therefore EXCLUDED from every total above,
    # rather than guessed at a 1:1 parity (§4.15). Zero means every invoice
    # in the window is accounted for.
    excluded_unconverted: int = 0
