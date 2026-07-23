from __future__ import annotations

from pydantic import BaseModel, Field


class DunningLevelIn(BaseModel):
    level: int = Field(ge=1, le=20)
    days_overdue: int = Field(ge=0, le=3650)
    tone: str = Field(default="reminder", pattern="^(reminder|firm|final)$")
    active: bool = True


class DunningLevelOut(BaseModel):
    level: int
    days_overdue: int
    tone: str
    active: bool


class DunningPolicyOut(BaseModel):
    # `is_default` is true when the org has configured no ladder and is running the
    # built-in default (so the UI can show "using defaults").
    is_default: bool
    levels: list[DunningLevelOut]


class DunningPolicyIn(BaseModel):
    levels: list[DunningLevelIn] = Field(default_factory=list, max_length=20)
