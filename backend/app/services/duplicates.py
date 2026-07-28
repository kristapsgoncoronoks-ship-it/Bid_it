"""Duplicate-invoice candidate detection.

Three distinct signals, all tenant-scoped and advisory (always available,
never a hard block — a human decides):

  • `exact`          — the SAME supplier already has an invoice with this number.
                       Almost always a true duplicate (double-upload, re-send).
  • `cross_supplier` — a DIFFERENT supplier has an invoice with this number. Usually
                       a coincidence (suppliers number independently), but worth
                       surfacing because it can mean a mis-assigned supplier.
  • `scored`         — E1.4: a SAME-vendor invoice with a DIFFERENT number but a
                       close amount and issue date — the re-issue/re-scan pattern
                       exact-number matching misses entirely (a vendor re-sends
                       the same spend under "INV-1042-R" or a re-typed number).

Distinguishing exact from cross_supplier is deliberate: the same number from the
same supplier and the same number from different suppliers are NOT the same
event, and the review UI treats them differently. `scored` is a third, weaker
signal layered on top — never conflated with the other two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.vendor import Vendor

# E1.4 scored-candidate tuning. Fixed for this order (not yet an admin
# setting) — named here so a future order can promote them without hunting.
_AMOUNT_TOLERANCE_PCT = Decimal("0.01")  # ±1% of the reference total
_DATE_PROXIMITY_DAYS = 14  # candidates issued further apart than this score 0


@dataclass
class Candidate:
    invoice_id: str
    vendor_id: str
    vendor_name: str
    invoice_number: str
    issue_date: str | None
    total: str
    currency: str
    status: str


@dataclass
class DuplicateReport:
    invoice_number: str
    exact: list[Candidate]  # same supplier + same number
    cross_supplier: list[Candidate]  # same number, a different supplier

    @property
    def has_exact(self) -> bool:
        return bool(self.exact)

    @property
    def has_cross_supplier(self) -> bool:
        return bool(self.cross_supplier)


def _candidate(inv: Invoice, vendor_name: str) -> Candidate:
    return Candidate(
        invoice_id=inv.id,
        vendor_id=inv.vendor_id,
        vendor_name=vendor_name,
        invoice_number=inv.invoice_number,
        issue_date=inv.issue_date.isoformat() if inv.issue_date else None,
        total=str(inv.total),
        currency=inv.currency,
        status=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
    )


async def candidates(
    db: AsyncSession,
    org_id: str,
    *,
    invoice_number: str,
    vendor_id: str | None = None,
    exclude_invoice_id: str | None = None,
) -> DuplicateReport:
    """Find same-number invoices in the org, split by supplier. `vendor_id` is the
    prospective supplier of the draft (so exact = that supplier); when unknown, all
    same-number invoices are reported as cross_supplier candidates."""
    number = (invoice_number or "").strip()
    if not number:
        return DuplicateReport(invoice_number=number, exact=[], cross_supplier=[])

    rows = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.org_id == org_id, Invoice.invoice_number == number)
            .order_by(Invoice.issue_date.desc())
        )
    )
    # Resolve vendor names in one query (tenant-scoped).
    vids = {r.vendor_id for r in rows}
    names: dict[str, str] = {}
    if vids:
        for vid, vname in await db.execute(
            select(Vendor.id, Vendor.name).where(Vendor.org_id == org_id, Vendor.id.in_(vids))
        ):
            names[vid] = vname

    exact: list[Candidate] = []
    cross: list[Candidate] = []
    for r in rows:
        if exclude_invoice_id and r.id == exclude_invoice_id:
            continue
        cand = _candidate(r, names.get(r.vendor_id, "—"))
        if vendor_id and r.vendor_id == vendor_id:
            exact.append(cand)
        else:
            cross.append(cand)
    return DuplicateReport(invoice_number=number, exact=exact, cross_supplier=cross)


@dataclass
class ScoredCandidate:
    invoice_id: str
    vendor_id: str
    vendor_name: str
    invoice_number: str
    issue_date: str | None
    total: str
    currency: str
    status: str
    score: int  # 0-100, advisory only — never a gate
    reason: str


async def scored_candidates(
    db: AsyncSession,
    org_id: str,
    *,
    vendor_id: str,
    total: Decimal,
    currency: str,
    issue_date: date | None,
    exclude_invoice_id: str | None = None,
    exclude_invoice_number: str | None = None,
) -> list[ScoredCandidate]:
    """E1.4: same-vendor invoices with a DIFFERENT number but a close amount and
    issue date — the re-issue/re-scan pattern exact-number matching (`candidates`)
    never sees. Advisory only: a human decides, nothing here blocks a save.

    Scoped to one vendor and one currency at the query — a cross-currency amount
    comparison is meaningless (§4.14), and a cross-vendor coincidence is noisier
    signal than this order is trying to surface (see WO-44 scope). Candidates
    outside BOTH the amount tolerance and the date window score 0 and are
    dropped, never returned at score 0.
    """
    if not vendor_id or total is None or currency is None:
        return []
    tolerance = total * _AMOUNT_TOLERANCE_PCT
    lo, hi = total - tolerance, total + tolerance
    rows = list(
        await db.scalars(
            select(Invoice).where(
                Invoice.org_id == org_id,
                Invoice.vendor_id == vendor_id,
                Invoice.currency == currency,
                Invoice.total >= lo,
                Invoice.total <= hi,
            )
        )
    )
    if not rows:
        return []
    vendor_name = (
        await db.scalar(select(Vendor.name).where(Vendor.org_id == org_id, Vendor.id == vendor_id))
        or "—"
    )

    out: list[ScoredCandidate] = []
    for r in rows:
        if exclude_invoice_id and r.id == exclude_invoice_id:
            continue
        if exclude_invoice_number and r.invoice_number == exclude_invoice_number:
            continue  # already surfaced by the exact-number path

        amount_diff = abs(Decimal(r.total) - total)
        if tolerance > 0:
            amount_score = round(60 * (1 - min(amount_diff / tolerance, Decimal(1))))
        else:
            amount_score = 60 if amount_diff == 0 else 0

        date_days: int | None = None
        date_score = 0
        if issue_date and r.issue_date:
            date_days = abs((r.issue_date - issue_date).days)
            if date_days <= _DATE_PROXIMITY_DAYS:
                # Integer arithmetic throughout — no float in the score path.
                date_score = (40 * (_DATE_PROXIMITY_DAYS - date_days)) // _DATE_PROXIMITY_DAYS

        score = int(amount_score) + date_score
        if score <= 0:
            continue

        amount_bit = (
            "same amount" if amount_diff == 0 else f"amount within {_AMOUNT_TOLERANCE_PCT:.0%}"
        )
        bits = ["Same vendor", amount_bit]
        if date_days is not None:
            bits.append("same day" if date_days == 0 else f"{date_days}d apart")
        reason = ", ".join(bits)

        out.append(
            ScoredCandidate(
                invoice_id=r.id,
                vendor_id=r.vendor_id,
                vendor_name=vendor_name,
                invoice_number=r.invoice_number,
                issue_date=r.issue_date.isoformat() if r.issue_date else None,
                total=str(r.total),
                currency=r.currency,
                status=r.status.value if hasattr(r.status, "value") else str(r.status),
                score=min(100, score),
                reason=reason,
            )
        )
    out.sort(key=lambda c: c.score, reverse=True)
    return out
