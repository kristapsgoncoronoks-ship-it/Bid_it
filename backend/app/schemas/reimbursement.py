"""Schemas for expense reimbursement payout batches (Phase 09)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReportVersion = Annotated[int, Field(strict=True, ge=1)]


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_ids: list[str] = Field(min_length=1, max_length=1000)
    report_versions: dict[str, ReportVersion] = Field(default_factory=dict, max_length=1000)
    method: str = Field(default="bank_transfer", min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("report_ids")
    @classmethod
    def valid_report_ids(cls, value: list[str]) -> list[str]:
        ids = [str(UUID(item)) for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Select each report once")
        return ids

    @field_validator("report_versions")
    @classmethod
    def valid_version_ids(cls, value: dict[str, int]) -> dict[str, int]:
        canonical = {str(UUID(key)): version for key, version in value.items()}
        if len(canonical) != len(value):
            raise ValueError("Send each report version once")
        return canonical

    @model_validator(mode="after")
    def complete_versions(self):
        if self.report_versions and set(self.report_versions) != set(self.report_ids):
            raise ValueError("Send the current version of every selected report")
        return self


class BatchPay(BaseModel):
    version: int
    reference: str | None = None
    method: str | None = None
    # Board R6 — the audited, explicit platform-admin escape hatch for the
    # maker≠checker control (mirrors payment_run's RunPay.override_sod).
    override_sod: bool = False


class ReimbursementReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    employee_name: str
    total: Decimal
    currency: str
    total_eur: Decimal | None
    status: str
    payment_reference: str | None
    reimbursed_at: datetime | None


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reference: str | None
    method: str
    status: str
    note: str | None
    total_eur: Decimal
    paid_at: datetime | None
    created_by: str | None
    exported_at: datetime | None = None
    export_count: int = 0
    last_msg_id: str | None = None
    version: int
    created_at: datetime
    report_count: int


class BatchDetailOut(BatchOut):
    reports: list[ReimbursementReportOut]


class EligibleReportOut(BaseModel):
    id: str
    title: str
    employee_name: str
    total: Decimal
    currency: str
    total_eur: Decimal | None
    status: str
    version: int


class EligibleReportPage(BaseModel):
    items: list[EligibleReportOut]
    total: int
    page: int
    page_size: int


class BatchPage(BaseModel):
    items: list[BatchOut]
    total: int
    page: int
    page_size: int
