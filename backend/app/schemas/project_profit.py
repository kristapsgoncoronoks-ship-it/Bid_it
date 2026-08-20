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
    # Non-empty only on a FROZEN P&L: per-figure deltas for what arrived after
    # the close — displayed drift, never silent drift.
    adjustments: dict[str, str] = Field(default_factory=dict)
    pnl_frozen_at: str | None = None
    # The latest accepted offer's total — None until an offer is accepted.
    estimated_revenue: str | None = None


class SplitIn(BaseModel):
    project_id: str
    percent: Decimal


class AllocationIn(BaseModel):
    """One invoice's allocation, all three levels in one write so they can never
    contradict each other. `splits` replaces all existing rows (empty list
    clears); `lines` tags only the lines it names."""

    project_id: str | None = None
    splits: list[SplitIn] | None = None
    lines: dict[str, str | None] | None = None


class OfferLineIn(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    amount: Decimal


class OfferIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    lines: list[OfferLineIn] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=2000)


class OfferOut(BaseModel):
    id: str
    number: str
    version: int
    status: str
    title: str | None = None
    currency: str
    total: str
    lines: list[dict] = Field(default_factory=list)
    note: str | None = None
    created_by: str | None = None
    created_at: str


class OfferTransitionIn(BaseModel):
    status: str


class PlanRowIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    amount: Decimal


class PlanRowOut(BaseModel):
    id: str
    label: str
    amount: str
    position: int


class PlanTrackingOut(BaseModel):
    """The contracted schedule vs. what was actually issued — the gap is a live
    receivable, and later (phase 5) the ADJUSTABLE starting point of the final
    invoice."""

    project_id: str
    rows: list[PlanRowOut] = Field(default_factory=list)
    contracted_total: str
    issued_total: str
    remaining: str
