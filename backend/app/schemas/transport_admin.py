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


# --------------------------------------------------------------------------- #
# Claimant documents (WO-AB, G2.10 slice 2) — what the `check_type="document"`
# checklist rules read
# --------------------------------------------------------------------------- #


class ClaimantDocumentOut(BaseModel):
    id: str
    entity_id: str
    kind: str
    # "" for a customer-scope document; an ISO-2 code for a country one.
    country: str
    sha256: str
    size: int
    mime: str | None = None
    filename: str | None = None
    valid_from: date | None = None
    # None = no stated expiry, which is NOT "expired" — see
    # `claimant_documents`'s own module docstring.
    valid_until: date | None = None
    uploaded_by: str | None = None


class ClaimantDocumentListOut(BaseModel):
    entity_id: str
    documents: list[ClaimantDocumentOut]
    # The catalogue, so the screen cannot advertise a kind the CHECK constraint
    # would refuse (`fuel_card_networks`' own "one list, not two" convention).
    kinds: list[str]


class ExpiringDocumentOut(BaseModel):
    """One row of §3.E's expiry chase board. `days_left` is NEGATIVE for a
    document that has already lapsed — the board keeps showing it, because a
    chase list that dropped a document the day it expired would go quiet at
    exactly the moment the claims it covers start being refused."""

    id: str
    entity_id: str
    kind: str
    country: str
    filename: str | None = None
    valid_until: date
    days_left: int


class ExpiringDocumentListOut(BaseModel):
    within_days: int
    documents: list[ExpiringDocumentOut]


class FeeRateSetIn(BaseModel):
    """One rung of the contingency-fee chain.

    `entity_id=None` writes the ORG STANDARD; `country=None` (or "") writes the
    customer default; both together write the per-(customer, country) override.
    Shape only — the service owns the business rules (an org rung may carry no
    country; the percentage and minimum are validated there).
    """

    entity_id: str | None = None
    country: str | None = Field(default=None, max_length=2)
    fee_pct: Decimal = Field(ge=0)
    fee_min: Decimal = Field(ge=0)


class FeeRateOut(BaseModel):
    id: str
    entity_id: str | None
    country: str
    fee_pct: Decimal
    fee_min: Decimal
    # How this price sits against the STANDARD — derived by comparing two rows,
    # never stored. `standard` / `discount` / `premium` / `no_standard`.
    # The discounts are percentages OFF the standard: 20 means 20% cheaper,
    # -10 means 10% dearer. None where there is no standard to compare with, or
    # where the standard figure is zero — "no discount" and "nothing to compare
    # with" are different statements.
    kind: str = "no_standard"
    standard_pct: Decimal | None = None
    standard_min: Decimal | None = None
    pct_discount: Decimal | None = None
    min_discount: Decimal | None = None


class FeeRateDiscountIn(BaseModel):
    """Negotiate a client off the standard. The discount is how the numbers are
    ARRIVED at; what gets stored is the resulting absolute pair, so a later
    change to the standard leaves this client where they were agreed.

    Negative is legal and means a premium above standard.
    """

    entity_id: str
    country: str | None = Field(default=None, max_length=2)
    discount_pct: Decimal
