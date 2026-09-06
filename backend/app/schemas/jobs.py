from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class JobEnqueue(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    payload: dict | None = None
    # Optional dedupe key: a second enqueue with the same (kind, key) is a no-op
    # while an earlier job is still live.
    idempotency_key: str | None = Field(default=None, max_length=120)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    status: str
    attempts: int
    max_attempts: int
    idempotency_key: str | None
    run_after: datetime
    last_error: str | None
    result_json: str | None
    created_at: datetime
    updated_at: datetime
    #: BE-004 (audit 2026-09-05): True when the enqueue matched an existing job
    #: with the same idempotency key — live OR finished — and scheduled nothing
    #: new. Served with 200, never 201. A finished job's work is not re-run by
    #: repeating its key: use a new key, or `POST /jobs/{id}/retry` for a
    #: failed/dead one.
    deduplicated: bool = False
