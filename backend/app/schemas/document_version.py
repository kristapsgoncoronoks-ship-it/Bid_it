from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentVersionOut(BaseModel):
    """One entry in a single-file slot's supersession history (Slice 5g)."""

    model_config = ConfigDict(from_attributes=True)

    version: int
    is_current: bool
    sha256: str
    size: int
    mime: str | None
    filename: str | None
    uploaded_by: str | None
    created_at: datetime
