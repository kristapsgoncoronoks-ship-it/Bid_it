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
