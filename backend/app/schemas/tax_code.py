from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["standard", "reduced", "zero", "exempt", "reverse_charge"]


class TaxCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    rate: Decimal
    category: str
    country: str | None = None
    active: bool
    version: int


class TaxCodeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=24)
    name: str = Field(min_length=1, max_length=120)
    rate: Decimal = Field(ge=0, le=100)
    category: Category = "standard"
    country: str | None = Field(default=None, max_length=2)


class TaxCodeActivate(BaseModel):
    """Archive (active=false) or restore (active=true) a code, version-checked."""

    active: bool
    version: int
