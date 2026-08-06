"""Request/response schemas for the transport admin/config + filing-artifact
routes (WO-77).

Money/litres fields are typed `Decimal` (the WO-76 `transport_claim`
convention) — pydantic v2 serializes them as JSON strings and parses string
inputs back to `Decimal`, so no float ever crosses the wire into the
services' `q2` quantization (master-context §4.9). Every figure is read
from (or handed verbatim to) the service layer; nothing here recomputes,
rounds or invents a number (§4.10 stays enforced where it lives — in the
services).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Receipt-control waivers (WO-58, R15) — claim-scoped
# --------------------------------------------------------------------------- #


class WaiverSetIn(BaseModel):
    supplier: str = Field(min_length=1, max_length=60)


class WaiverOut(BaseModel):
    id: str
    claim_id: str
    supplier: str


class RemovedOut(BaseModel):
    # True when a row was actually deleted; False = idempotent no-op — the
    # services' own return contract (a repeat removal audits nothing).
    removed: bool


# --------------------------------------------------------------------------- #
# Manual status codes (WO-59, R17/R12) — claim-scoped
# --------------------------------------------------------------------------- #


class StatusCodeSetIn(BaseModel):
    code: str = Field(min_length=1, max_length=2)
    # R12 — a SOFT worklist reminder; the service stamps it, never acts on it.
    action_deadline: date | None = None


class StatusCodesOut(BaseModel):
    # The vocabulary read: system-derived AUTO codes (never settable) and the
    # manual workflow codes `set_status_code` accepts. No label mapping exists
    # in this codebase (WO-77 decision 5) — the codes ARE the vocabulary.
    auto: list[str]
    manual: list[str]


# --------------------------------------------------------------------------- #
# Checklist rules admin (WO-60, R45)
# --------------------------------------------------------------------------- #


class ChecklistRuleOut(BaseModel):
    id: str
    key: str
    label: str
    scope: str
    check_type: str
    reference: str | None = None
    active: bool
    sort: int


class ChecklistRuleActiveIn(BaseModel):
    active: bool


# --------------------------------------------------------------------------- #
# Receipt-control cadences + the persisted control grid (WO-72, G3.5)
# --------------------------------------------------------------------------- #


class CadenceSetIn(BaseModel):
    # The harvested closed set is enforced by the service (`invalid_cadence`);
    # the schema only bounds the shape (DoD §3: schemas catch shape, services
    # catch the business rule).
    cadence: str = Field(min_length=1, max_length=20)


class CadenceOut(BaseModel):
    id: str
    supplier: str
    cadence: str


class ReceiptControlOut(BaseModel):
    id: str
    entity_id: str
    supplier: str
    period: str
    slot: str
    country: str
    status: str
    txn_count: int
    waived: bool
    note: str | None = None


class ControlOverrideIn(BaseModel):
    # A `None` field leaves that column unchanged — the service's own
    # contract; `waived`/`note` have exactly one writer (§3.J item 4,
    # "manual overrides survive re-runs").
    waived: bool | None = None
    note: str | None = None


# --------------------------------------------------------------------------- #
# Note→invoice-ref overrides (WO-52, R16/C4)
# --------------------------------------------------------------------------- #


class NoteOverrideSetIn(BaseModel):
    entity_id: str
    supplier: str = Field(min_length=1, max_length=60)
    refund_country: str = Field(min_length=2, max_length=2)
    invoice_ref: str = Field(min_length=1, max_length=120)
    target_invoice_id: str


class NoteOverrideOut(BaseModel):
    id: str
    entity_id: str
    supplier: str
    refund_country: str
    invoice_ref: str
    target_invoice_id: str


# --------------------------------------------------------------------------- #
# Engine tie-out expectations (WO-66 regime 2, R25)
# --------------------------------------------------------------------------- #


class TieOutExpectationSetIn(BaseModel):
    entity_id: str
    supplier: str = Field(min_length=1, max_length=60)
    period: str = Field(min_length=7, max_length=7)  # "YYYY-MM" (service re-validates)
    currency: str = Field(min_length=3, max_length=3)
    expected_lines: int
    expected_gross_local: Decimal | None = None
    # Default mirrors the service's own (`GROSS_TOLERANCE_MIN` = 0.02); the
    # harvested band [0.02, 0.05] is enforced there (`invalid_gross_tolerance`).
    gross_local_tolerance: Decimal = Decimal("0.02")
    expected_net_eur: Decimal | None = None
    expected_gross_eur: Decimal | None = None
    expected_diesel_litres: Decimal | None = None


class TieOutExpectationOut(BaseModel):
    id: str
    entity_id: str
    supplier: str
    period: str
    currency: str
    expected_lines: int
    expected_gross_local: Decimal | None = None
    gross_local_tolerance: Decimal
    expected_net_eur: Decimal | None = None
    expected_gross_eur: Decimal | None = None
    expected_diesel_litres: Decimal | None = None


# --------------------------------------------------------------------------- #
# Customer lifecycle + per-country activation (WO-73, R44)
# --------------------------------------------------------------------------- #


class ActivationIn(BaseModel):
    active: bool


class CountryActivationOut(BaseModel):
    id: str
    country: str
    status: str


class LifecycleOut(BaseModel):
    entity_id: str
    # None = never onboarded — a MEANINGFUL absence (the R44 gate treats it
    # exactly like not-active), never a 404: the 404 keys off the entity.
    id: str | None = None
    status: str | None = None
    countries: list[CountryActivationOut]
