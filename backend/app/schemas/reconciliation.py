from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BankLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    statement_id: str
    line_date: date
    description: str
    amount: Decimal  # signed: credit +, debit −
    direction: str
    balance: Decimal | None = None
    status: str
    matched_kind: str | None = None
    matched_id: str | None = None


class BankStatementOut(BaseModel):
    id: str
    filename: str
    source_format: str
    method: str
    line_count: int
    created_by: str | None = None
    created_at: datetime
    unmatched: int = 0
    matched: int = 0
    ignored: int = 0


class ImportResult(BaseModel):
    statement_id: str
    method: str
    imported: int
    warnings: list[str] = []


class MatchCandidateOut(BaseModel):
    kind: str
    id: str
    amount: Decimal
    date: date
    reference: str | None = None
    days_off: int
    score: int


class MatchRequest(BaseModel):
    kind: str = Field(pattern="^(receipt|reimbursement)$")
    target_id: str
