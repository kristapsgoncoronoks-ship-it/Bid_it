from __future__ import annotations

from pydantic import BaseModel


class DocIssueOut(BaseModel):
    kind: str
    entity_id: str
    problem: str
    detail: str = ""


class IntegrityReportOut(BaseModel):
    checked: int
    ok: int
    healthy: bool
    issues: list[DocIssueOut] = []


class IntegrityQueuedOut(BaseModel):
    """PERF-009: the sweep was too large to run inside the request, so the
    matching background job was queued instead (HTTP 202). `created` is False
    when today's job for this sweep already existed — the queue's idempotency
    handed it back rather than scheduling the work twice."""

    queued: bool = True
    job_id: str
    job_kind: str
    references: int
    sync_limit: int
    created: bool
