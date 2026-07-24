from __future__ import annotations

from pydantic import BaseModel, EmailStr


class ErasureRequest(BaseModel):
    email: EmailStr


class ErasureLocationOut(BaseModel):
    key: str
    label: str
    matched: int
    action: str  # erase | retain | blocked
    reason: str | None = None


class ErasureReportOut(BaseModel):
    email: str
    on_hold: bool
    executed: bool
    locations: list[ErasureLocationOut]
