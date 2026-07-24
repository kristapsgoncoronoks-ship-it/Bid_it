"""Duplicate-invoice candidate detection.

Two distinct signals for an invoice number, both tenant-scoped and advisory
(always available, never a hard block — a human decides):

  • `exact`          — the SAME supplier already has an invoice with this number.
                       Almost always a true duplicate (double-upload, re-send).
  • `cross_supplier` — a DIFFERENT supplier has an invoice with this number. Usually
                       a coincidence (suppliers number independently), but worth
                       surfacing because it can mean a mis-assigned supplier.

Distinguishing the two is deliberate: the same number from the same supplier and
the same number from different suppliers are NOT the same event, and the review UI
treats them differently.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.vendor import Vendor


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
