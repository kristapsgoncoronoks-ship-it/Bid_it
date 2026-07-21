"""Partner (counterparty) workflow — pre-invoicing document gates + penalty invoices.

A partner may require formal documents signed before any invoice is issued to it
(a framework contract and/or an acceptance act). `readiness()` reports whether the
required documents are signed; `create_issued` refuses to issue to a partner that
is not ready.

Penalty invoicing bills accrued late-payment interest aggregated across a
partner's OVERDUE invoices. It is opt-in per partner and — per policy — may only
be generated once a contract is signed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import money
from app.models.issued_invoice import IssuedInvoice, IssuedInvoiceLine
from app.models.partner import Partner
from app.services import issued_status
from app.services import issuer as issuer_svc

CONTRACT = "contract"
ACCEPTANCE = "acceptance_act"
DOC_LABELS = {CONTRACT: "Contract", ACCEPTANCE: "Acceptance act"}

_ZERO = Decimal("0")


def required_kinds(p: Partner) -> list[str]:
    """Ordered prerequisite document kinds for this partner (contract first)."""
    kinds = []
    if p.requires_contract:
        kinds.append(CONTRACT)
    if p.requires_acceptance:
        kinds.append(ACCEPTANCE)
    return kinds


def _signed_kinds(p: Partner) -> set[str]:
    # Assumes p.documents is loaded (selectinload).
    return {d.kind for d in p.documents if d.status == "signed"}


def has_signed_contract(p: Partner) -> bool:
    return CONTRACT in _signed_kinds(p)


def missing_signed(p: Partner) -> list[str]:
    """Required document kinds that do not yet have a SIGNED document."""
    signed = _signed_kinds(p)
    return [k for k in required_kinds(p) if k not in signed]


def is_ready(p: Partner) -> bool:
    """True when every prerequisite document is signed (or none is required)."""
    return not missing_signed(p)


@dataclass
class Readiness:
    ready: bool
    required: list[str]
    signed: list[str]
    missing: list[str]


def readiness(p: Partner) -> Readiness:
    signed = _signed_kinds(p)
    req = required_kinds(p)
    return Readiness(
        ready=is_ready(p),
        required=req,
        signed=[k for k in req if k in signed],
        missing=missing_signed(p),
    )


async def get_partner(db: AsyncSession, org_id: str, partner_id: str) -> Partner | None:
    return await db.scalar(
        select(Partner)
        .where(Partner.id == partner_id, Partner.org_id == org_id)
        .options(selectinload(Partner.documents))
    )


# --- Penalty invoicing ---------------------------------------------------------


@dataclass
class PenaltyLine:
    invoice_id: str
    number: str
    days_overdue: int
    outstanding: Decimal
    penalty: Decimal


@dataclass
class PenaltySummary:
    currency: str
    total_penalty: Decimal
    total_outstanding: Decimal
    max_days_overdue: int
    lines: list[PenaltyLine] = field(default_factory=list)


async def penalty_summary(
    db: AsyncSession, org_id: str, partner_id: str, today: date | None = None
) -> PenaltySummary:
    """Accrued late-payment interest across a partner's OVERDUE invoices.

    Overdue days and penalty are computed per invoice, then aggregated by partner.
    Penalty invoices themselves (kind='penalty') are excluded so interest never
    compounds on interest.
    """
    today = today or date.today()
    rows = list(
        await db.scalars(
            select(IssuedInvoice).where(
                IssuedInvoice.org_id == org_id,
                IssuedInvoice.partner_id == partner_id,
                IssuedInvoice.kind == "standard",
            )
        )
    )
    lines: list[PenaltyLine] = []
    total_pen, total_out, max_days = _ZERO, _ZERO, 0
    currency = "EUR"
    for inv in rows:
        if issued_status.status_of(inv, today) != issued_status.OVERDUE:
            continue
        pen = issued_status.penalty_of(inv, today)
        if pen <= _ZERO:
            continue
        days = issued_status.days_overdue_of(inv, today)
        out = issued_status.outstanding_of(inv)
        currency = inv.currency
        lines.append(PenaltyLine(inv.id, inv.number, days, out, pen))
        total_pen += pen
        total_out += out
        max_days = max(max_days, days)
    return PenaltySummary(currency, money.q2(total_pen), money.q2(total_out), max_days, lines)


class PenaltyBlocked(Exception):
    """Raised when a penalty invoice cannot be generated (policy/state)."""


async def generate_penalty_invoice(
    db: AsyncSession, org_id: str, partner: Partner, today: date | None = None
) -> IssuedInvoice:
    """Create a penalty (late-interest) invoice for a partner. Requires the
    partner to allow penalties AND to have a signed contract."""
    today = today or date.today()
    if not partner.penalty_enabled:
        raise PenaltyBlocked("Penalty invoicing is not enabled for this partner.")
    if not has_signed_contract(partner):
        raise PenaltyBlocked(
            "A signed contract is required before a penalty invoice can be generated."
        )

    summary = await penalty_summary(db, org_id, partner.id, today)
    if summary.total_penalty <= _ZERO:
        raise PenaltyBlocked("This partner has no accrued late-payment interest to bill.")

    profile = await issuer_svc.get_or_create(db, org_id)
    number = f"{profile.invoice_prefix}{today.year}-{profile.next_number:04d}"
    profile.next_number += 1

    detail = "; ".join(
        f"{ln.number} ({ln.days_overdue}d, {summary.currency} {ln.penalty})" for ln in summary.lines
    )
    note = f"Late-payment interest on overdue invoices: {detail}."

    inv = IssuedInvoice(
        org_id=org_id,
        partner_id=partner.id,
        kind="penalty",
        number=number,
        issue_date=today,
        due_date=today,
        currency=summary.currency,
        buyer_name=partner.name,
        buyer_email=partner.email,
        buyer_vat_number=partner.vat_number,
        buyer_address_line1=partner.address_line1,
        buyer_city=partner.city,
        buyer_postal_code=partner.postal_code,
        buyer_country=partner.country,
        seller_json=json.dumps(issuer_svc.seller_snapshot(profile)),
        vat_scheme="exempt",  # late-payment interest is outside the scope of VAT
        note=note,
        subtotal=summary.total_penalty,
        tax_total=_ZERO,
        total=summary.total_penalty,
        lines=[
            IssuedInvoiceLine(
                position=1,
                description=f"Late-payment interest on {len(summary.lines)} overdue invoice(s)",
                quantity=Decimal("1"),
                unit="C62",
                unit_price=summary.total_penalty,
                vat_rate=_ZERO,
                net_amount=summary.total_penalty,
            )
        ],
    )
    db.add(inv)
    return inv
