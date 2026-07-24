from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: str
    seq: int
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    meta: dict | None
    at: datetime


class AuditListOut(BaseModel):
    items: list[AuditEventOut]
    total: int


class ChainStatusOut(BaseModel):
    ok: bool
    events: int
    broken_at_seq: int | None = None
    detail: str | None = None
