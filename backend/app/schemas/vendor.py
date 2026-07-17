from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tax_id: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    category: str | None = Field(default=None, max_length=80)


class VendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    tax_id: str | None
    country: str | None
    category: str | None
