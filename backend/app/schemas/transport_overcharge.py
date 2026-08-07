"""Wire schemas for the supplier overcharge surface (WO-82, G4.5/R41).

Field-for-field from `app/services/transport/contract_audit.py`'s dataclasses
and `app/models/transport/overcharge.py`'s columns — this module shapes the wire
and computes nothing (engineering-rules §3).

Every amount and every €/L rate is typed `Decimal`, so pydantic v2 serializes it
as a JSON STRING and no float ever appears in a money path (master-context
§4.9). The service already quantized each one (`money.q2` for euros,
`money.q(..., 0.0001)` for rates).

`currency` is carried explicitly and is always `"EUR"`. It is not decoration:
these lines can arrive in several document currencies, and every figure here is
derived from the EUR price columns — so the response states the basis instead of
leaving a consumer to assume it (§4.14). A finding's own
`document_currency` rides beside it as PROVENANCE and is never a total.

`price_basis` and `legal_framing` ride the audit response because §3.G G1
requires the basis to be *"stated on any new report surface"* and R53 requires
this analysis's framing (*"money the supplier owes"*, as against negotiation
evidence) never to be flattened.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ContractTermIn(BaseModel):
    """One agreed commercial term. §2.5's *"two term types only"* — at least one
    of the two figures must be present; the SERVICE enforces that (and the
    ranges), so the refusal code is the service's own (`term_has_no_figure`,
    `invalid_term_rate`, `invalid_product_group`, `invalid_country`)."""

    supplier: str = Field(min_length=1, max_length=60)
    country: str = Field(min_length=2, max_length=2)
    product_group: str = Field(min_length=1, max_length=20)
    # A LIKE pattern over the station column; "" (the default) = every station.
    station_like: str = Field(default="", max_length=120)
    expected_discount_eur_l: Decimal | None = None
    max_net_eur_l: Decimal | None = None
    active: bool = True


class ContractTermOut(BaseModel):
    id: str
    supplier: str
    country: str
    station_like: str
    product_group: str
    expected_discount_eur_l: Decimal | None = None
    max_net_eur_l: Decimal | None = None
    active: bool


class BreachOut(BaseModel):
    """One line that breaches one agreed term. Basis: NET EUR/L, final — VAT
    excluded, rebates applied."""

    fuel_transaction_id: str
    entity_id: str
    supplier: str
    country: str
    station: str
    product_group: str
    txn_date: date
    qty: Decimal
    # Provenance only — never summed, never the unit of any amount below.
    document_currency: str
    eur_l_doc: Decimal
    eur_l_eff: Decimal
    flag: str
    agreed_eur_l: Decimal
    actual_eur_l: Decimal
    gap_eur_l: Decimal
    recover_eur: Decimal


class ContractAuditOut(BaseModel):
    period: str
    supplier: str | None = None
    # Always "EUR" — the basis of `recover_eur`, stated not assumed (§4.14).
    currency: str
    price_basis: str
    legal_framing: str
    breaches: list[BreachOut]
    # The €-exposure DETECTED — NOT cash recovered. The booked-cash north star
    # is `recovered_total` (the dashboard's `overcharges_eur`); the two are
    # deliberately never reported under one name (R52's label-them-distinctly).
    recover_eur: Decimal
    lines_audited: int
    lines_without_terms: int
    lines_skipped_zero_qty: int


class OverchargeClaimOpenIn(BaseModel):
    supplier: str = Field(min_length=1, max_length=60)
    period: str = Field(min_length=7, max_length=7)


class OverchargeClaimAdvanceIn(BaseModel):
    """`to_status` is validated against the harvested six BY THE SERVICE, and the
    edge itself against `overcharge.TRANSITIONS` — the wire enum is deliberately
    not duplicated here, so the vocabulary cannot drift (§4.20)."""

    to_status: str = Field(min_length=1, max_length=16)
    # Only a `recovered` transition carries one; any other target refuses it.
    recovered_eur: Decimal | None = None
    note: str | None = None


class OverchargeClaimOut(BaseModel):
    id: str
    supplier: str
    period: str
    status: str
    # Frozen at open — the euro the demand quotes (never re-derived).
    detected_eur: Decimal
    lines_count: int
    # Booked cash; null until (and unless) the claim-back reaches `recovered`.
    recovered_eur: Decimal | None = None
    note: str | None = None
    currency: str
    created_at: datetime


class OverchargeTotalOut(BaseModel):
    """§2.4's booked-cash north star, alone on the wire so a consumer can read it
    without loading the worklist."""

    year: int | None = None
    currency: str
    recovered_eur: Decimal
