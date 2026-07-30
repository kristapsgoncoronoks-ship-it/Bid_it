"""The document-presence gate (G2.6 slice 3, R10; `BA_fleet_fuel.md` C8).

`enforce_document_presence` is a pure CHECK (never writes) run inside
`lock.submit_claim`, after the R6 mop-up/duplicate-block gate and BEFORE the
G2.5 freeze — a claim missing a document for any of its real, resolved
invoices never freezes or locks (the same "nothing mutates before the last
gate passes" discipline every prior G2.6 slice states explicitly).

WHY THIS READS THE CLAIM'S OWN MATERIALIZED LINES, NOT `submit_claim`'S
`invoices` PARAMETER
------------------------------------------------------------------------
`lock.submit_claim` still takes a caller-supplied `invoices: list[tuple[...]]`
that is NOT (yet) derived from `vat_claim_lines` (see `lock.py`'s own
docstring). But the thing that actually gets FROZEN as the claim's content
is the lines table — `freeze.freeze_claim_lines` sums `VatRefundClaimLine`
rows, never the `invoices` tuple. Checking document presence against that
same authoritative source (the claim's currently-unfrozen lines with a
resolved, non-NULL `invoice_id`) is therefore the faithful check: it
validates exactly what is about to be frozen, with no new coupling to
`submit_claim`'s existing parameter shape.

WHY AN `UNMATCHED` LINE IS INVISIBLE TO THIS GATE
--------------------------------------------------
A synthetic/`UNMATCHED` claim line (`invoice_id IS NULL`) is not a real,
registered invoice — it has no document to be missing in the first place.
Blocking submission on an unresolved synthetic line at all is a SEPARATE,
not-yet-built gate (R3's `is_synthetic()` is still not wired as an actual
submission-blocking consumer anywhere — see WO-58's own "out of scope"
note); this gate only ever inspects lines that DID resolve to a real
`Invoice` row.

WHY THE DOCS CHECK IS ONE BATCH QUERY, NOT A LOOP
---------------------------------------------------
`app.services.extraction.invoice_ids_with_documents` takes the whole
distinct `invoice_id` set in one query (`WHERE invoice_id IN (...) AND
source_sha256 IS NOT NULL`) — Fleet Fuel's own `docs_index()` was built
specifically to avoid an N+1 scan over each locked invoice; this codebase
preserves that shape even though the underlying table (`ExtractionRun`)
differs entirely from Fleet Fuel's `invoice_documents` vault index.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.transport.vat_claim import VatRefundClaimLine
from app.services import extraction
from app.services import invoices as ap_invoices


async def _resolved_invoice_ids(db: AsyncSession, org_id: str, claim_id: str) -> list[str]:
    """Distinct, non-NULL `invoice_id`s among the claim's currently-unfrozen
    lines — the real, resolved lines about to be frozen (never an
    `UNMATCHED` line, whose `invoice_id` is always NULL)."""
    rows = await db.scalars(
        select(VatRefundClaimLine.invoice_id)
        .where(
            VatRefundClaimLine.org_id == org_id,
            VatRefundClaimLine.claim_id == claim_id,
            VatRefundClaimLine.frozen_at.is_(None),
            VatRefundClaimLine.invoice_id.is_not(None),
        )
        .distinct()
    )
    return sorted({r for r in rows if r is not None})


async def enforce_document_presence(db: AsyncSession, org_id: str, claim_id: str) -> None:
    """R10 — raise (409, `code="invoice_document_missing"`) naming every
    invoice number among the claim's resolved lines that has ZERO vaulted
    documents. A claim with no resolved lines at all (every line still
    `UNMATCHED`, or no lines built yet) passes trivially — there is nothing
    for this gate to check."""
    invoice_ids = await _resolved_invoice_ids(db, org_id, claim_id)
    if not invoice_ids:
        return

    documented = await extraction.invoice_ids_with_documents(db, org_id, invoice_ids)
    missing_ids = [i for i in invoice_ids if i not in documented]
    if not missing_ids:
        return

    names: list[str] = []
    for invoice_id in missing_ids:
        inv = await ap_invoices.get_by_id(db, org_id, invoice_id)
        names.append(inv.invoice_number if inv is not None else invoice_id)

    raise ConflictError(
        f"Missing vaulted document for invoice(s): {', '.join(sorted(names))}",
        code="invoice_document_missing",
    )
