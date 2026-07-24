from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "error"]


class ValidationFinding(BaseModel):
    severity: Severity
    code: str
    message: str
    field: str | None = None


class ValidationSettings(BaseModel):
    ai_validation_enabled: bool
    human_validation_enabled: bool


class ValidationSettingsUpdate(BaseModel):
    ai_validation_enabled: bool | None = None
    human_validation_enabled: bool | None = None


class ValidationDecision(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=1000)


class ValidationState(BaseModel):
    status: str
    findings: list[ValidationFinding]
    validated_by: str | None = None
    validated_at: datetime | None = None
