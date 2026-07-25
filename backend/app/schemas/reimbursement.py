"""Schemas for expense reimbursement payout batches (Phase 09)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class BatchCreate(BaseModel):
    report_ids: list[str]
    method: str = "bank_transfer"
    note: str | None = None


class BatchPay(BaseModel):
    version: int
    reference: str | None = None
    method: str | None = None


class ReimbursementReportOut(BaseModel):
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
