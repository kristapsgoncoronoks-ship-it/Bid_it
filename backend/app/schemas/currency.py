from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CurrencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    symbol: str | None = None
    decimal_places: int
    active: bool
    version: int
    # C1.5 (WO-23): identity provenance from the ONE currency registry
    # (`fx.CURRENCY_BY_CODE`) — True = known non-ECB/indicative rate, False =
    # known ECB-published rate, None = code outside the shared registry (no
    # provenance to report; its EUR conversion already fails closed to NULL).
    indicative: bool | None = None


class CurrencyCreate(BaseModel):
    code: str = Field(min_length=3, max_length=3)
    name: str = Field(min_length=1, max_length=60)
    symbol: str | None = Field(default=None, max_length=8)
    decimal_places: int = Field(default=2, ge=0, le=4)


class CurrencyActivate(BaseModel):
    """Archive (active=false) or restore (active=true) a currency, version-checked."""

    active: bool
    version: int
