from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RetentionCategoryOut(BaseModel):
    key: str
    label: str
    retain_days: int | None = None       # None = keep forever (no policy)
    purgeable_now: int = 0               # rows this policy would purge today


class LegalHoldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    reason: str
    active: bool
    placed_by: str | None = None
    released_by: str | None = None
    released_at: datetime | None = None
    created_at: datetime


class RetentionOut(BaseModel):
    categories: list[RetentionCategoryOut]
    on_hold: bool
    holds: list[LegalHoldOut]


class PolicyUpdate(BaseModel):
    category: str
    # 0 (or negative) removes the policy = keep forever again.
    retain_days: int = Field(ge=0, le=36525)   # up to ~100 years


class HoldCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class PurgeResult(BaseModel):
    held: bool
    purged: dict[str, int]
