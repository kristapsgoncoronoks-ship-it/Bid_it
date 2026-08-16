"""Wire shapes for project profitability (phase 1) — industry-neutral, like
everything in this feature: no field names an industry, examples live in docs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class CostEntryIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    category: str = Field(default="other", max_length=16)
    # Decimal on the wire as a string; negative allowed (a correction is a cost
    # line too), zero refused server-side.
    amount: Decimal
    entry_date: date | None = None
    note: str | None = Field(default=None, max_length=1000)


class CostEntryOut(BaseModel):
    id: str
    label: str
    category: str
    amount: str
    currency: str
    entry_date: str | None = None
    note: str | None = None
    created_by: str | None = None
    created_at: str


class ProjectDocumentOut(BaseModel):
    id: str
    kind: str
    filename: str
    content_type: str | None = None
    uploaded_by: str | None = None
    created_at: str


class ProjectPnlOut(BaseModel):
    """The live P&L. Figures are decimal STRINGS (no float ever touches money on
    this wire), and `basis` is stated by the server so no screen can claim a
    basis the numbers don't have."""

    project_id: str
    code: str
    name: str
    status: str
    revenue: str
    credited: str
    costs: str
    invoice_costs: str
    expense_costs: str
    manual_costs: str
    profit: str
    margin_pct: str | None = None
    basis: str
