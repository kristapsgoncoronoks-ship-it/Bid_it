"""Minimal read-only AP invoice queries shared across domains.

This module exists so a caller OUTSIDE the AP domain never has to import
`app.models.invoice` directly — ADR-P3 rule 2 (`docs/plan/plan-a/ARCH_plan.md`
"the transport vertical reads the core through services... never a raw model
join") names an `invoice_service` seam that did not exist in this codebase
until the transport vertical's note→invoice resolution
(`app.services.transport.invoice_match`, G2.4) needed exactly this: "which
invoices are REGISTERED for this vendor" without a cross-domain model import.

Business logic for invoice CREATE/UPDATE/the review-and-approval lifecycle
stays where it already lives (`app.services.invoice_workflow`, the `invoices`
route); this module is deliberately small and read-only.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice


async def list_by_vendor(db: AsyncSession, org_id: str, vendor_id: str) -> list[Invoice]:
    """Every invoice for one vendor, ordered by `invoice_number` for
    deterministic resolution. A "registered invoice" in the R2/R16 sense
    (`docs/plan/shared/specs/BA_fleet_fuel.md` C1/C3) is exactly a row here."""
    rows = await db.scalars(
        select(Invoice)
        .where(Invoice.org_id == org_id, Invoice.vendor_id == vendor_id)
        .order_by(Invoice.invoice_number)
    )
    return list(rows)


async def get_by_id(db: AsyncSession, org_id: str, invoice_id: str) -> Invoice | None:
    """Org-scoped lookup by id — returns `None` rather than raising (mirrors
    `app.services.issuer.get_by_id`'s contract) so a caller decides the 404
    shape for its own domain."""
    return await db.scalar(
        select(Invoice).where(Invoice.org_id == org_id, Invoice.id == invoice_id)
    )
