"""Request/response schemas for the transport claim-lifecycle routes (WO-76).

Money fields are typed `Decimal` (the `payment_run.RunOut.total_eur`
convention) — pydantic v2 serializes them as JSON strings, never float
(master-context §4.9). Every figure is READ from the claim/line rows the
services wrote; nothing here recomputes, rounds or invents a number (§4.10
stays enforced where it lives — in the services)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ClaimCreateIn(BaseModel):
    entity_id: str
    # ISO 3166-1 alpha-2 of the REFUNDING member state; shape at the boundary,
    # the service upper-cases and the business rule (real country, cross-border)
    # stays with the service/domain layer (DoD: schemas catch shape).
    refund_country: str = Field(min_length=2, max_length=2)
    # "YYYY-Qn" or "YYYY-YEAR" — re-validated by claim.validate_ref_period
    # (422 `invalid_period`); the schema only bounds the length.
    ref_period: str = Field(min_length=7, max_length=9)


class SubmitInvoiceIn(BaseModel):
    supplier: str = Field(min_length=1)
    invoice_ref: str = Field(min_length=1)
    fuel_transaction_id: str = Field(min_length=1)


class ClaimSubmitIn(BaseModel):
    # Shape at the boundary (min_length=1); the service's own `empty_claim_set`
    # refusal remains as the defense-in-depth business rule (DoD §3: schemas
    # catch shape AND the invariant is enforced in the service).
    invoices: list[SubmitInvoiceIn] = Field(min_length=1)
    # R8's explicit admin override — recorded by the service in `status_note`,
    # never silent. Deliberately gated by the same VAT_SUBMIT permission as the
    # submit itself (no harvested rule assigns it a stricter one; recorded in
    # WO-76's out-of-scope list).
    override_minimum: bool = False


class ClaimOut(BaseModel):
    id: str
    entity_id: str
    refund_country: str
    ref_period: str
    status: str
    status_code: str | None = None
    status_note: str | None = None
    decision_date: date | None = None
    action_deadline: date | None = None
    submitted_date: date | None = None
    approved_date: date | None = None
    paid_date: date | None = None
    paid_amount: Decimal | None = None
    # Frozen at submission (G2.5) — NULL means "not yet frozen", not zero.
    vat_eur: Decimal | None = None
    vat_local: Decimal | None = None
    currency: str | None = None
    fee_pct: Decimal | None = None
    fee_min: Decimal | None = None
    fee_eur: Decimal | None = None
    created_at: datetime


class ClaimLineOut(BaseModel):
    id: str
    claim_id: str
    invoice_ref: str
    vat_id: str | None = None
    invoice_id: str | None = None
    goods_code: str | None = None
    product_group: str | None = None
    net_eur: Decimal
    vat_eur: Decimal
    net_local: Decimal | None = None
    vat_local: Decimal | None = None
    currency: str | None = None
    frozen_at: datetime | None = None
    # WO-L §13: set when the member state's decision rejected this line.
    rejected_at: datetime | None = None
    # WO-L §11 (option b): the suppliers behind an UNMATCHED bucket — a
    # work-item hint, never a filable attribute. None on resolved lines.
    unmatched_suppliers: list[str] | None = None


class ClaimDecisionIn(BaseModel):
    """WO-L §13: the member state's decision on a submitted claim."""

    outcome: str  # approved | rejected | partial (validated in the service)
    rejected_refs: list[str] | None = None
    decision_date: date | None = None


class ClaimPaymentIn(BaseModel):
    """WO-T: the refund landed. `paid_amount` is REQUIRED and typed `Decimal`
    (so it arrives as an exact string, §4.9) — the amount that reached the bank
    is a fact to record, not a figure to derive from the approved base. Deriving
    it would quietly assert the two matched, and the reason to record a payment
    at all is that they sometimes do not."""

    paid_amount: Decimal
    paid_date: date | None = None


class ChecklistItemOut(BaseModel):
    key: str
    label: str
    scope: str
    ok: bool
    reason: str | None = None


class StageOut(BaseModel):
    # One of the system-derived AUTO codes 1A/1B/1C/1E (R17) — a read-only
    # preview for a draft claim, never a settable value.
    stage: str
