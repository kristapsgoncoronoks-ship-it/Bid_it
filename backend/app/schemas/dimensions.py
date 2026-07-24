from __future__ import annotations

from pydantic import BaseModel, Field

# One place to declare the dimension fields for every schema that carries them
# (invoices, expense items). Mirrors app.core.dimensions.DIMENSIONS.
_F = Field(default=None, max_length=80)


class DimensionFields(BaseModel):
    cost_center: str | None = _F
    department: str | None = _F
    project: str | None = _F
    vehicle: str | None = _F
    property_ref: str | None = _F
