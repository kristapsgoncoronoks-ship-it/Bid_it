"""The fuel-transaction READ accessor (WO-79).

WO-50 shipped `fuel_transactions` — the typed line item every VAT refund claim
is built from — and `fuel_ingest.ingest_transaction` as its single writer. Two
years of transport work later, NO code path returned one: `claim_lines.
build_claim_lines` reads them to materialize `vat_claim_lines`, `receipt_control`
counts them per slot, `close` closes over them — and every one of those surfaces
serves a DERIVED figure. `grep -rn "FuelTransaction" app/api` was empty before
this module existed.

That gap had two concrete costs, and this module exists to close both:

1. `tests/test_tenancy_parity.py` carried `fuel_transactions` as an EXEMPT row
   with the reason "no route RETURNS these rows" — so the table's behavioural
   isolation was proven by the ORM guard and Postgres RLS, but never over the
   real HTTP read path every other claim table is proven on. WO-79 converts that
   exemption into a genuine probe, which is only possible because this accessor
   exists.
2. `lock.submit_claim` keys its per-invoice locks on caller-supplied
   `(supplier, invoice_ref, fuel_transaction_id)` tuples. With nothing
   enumerating transactions, the SPA's submit dialog (WO-78) had to ask an
   operator to TYPE a UUID. This accessor is what turns that into a selection.

WHY THIS IS A READ-ONLY MODULE, AND STAYS ONE
------------------------------------------------
Ingestion belongs to the parser/statement tier (`fuel_ingest`,
`statement_ingest`), which enforces the natural-key idempotency, the money
quantization and the `product_group` derivation. A route-tier writer would have
to duplicate all three or bypass them; neither is acceptable, so this module
offers no create/update/delete and the WO-79 route surface is a single GET. No
mutation therefore means no audit event here (§4.16 governs mutations) — the
read is genuinely side-effect-free, exactly like `claim_lines.list_claim_lines`.

WHY `period` ACCEPTS TWO SHAPES
---------------------------------
`FuelTransaction.period` is `"YYYY-MM"` (the accounting month, half the natural
key). A CLAIM's `ref_period` is `"YYYY-Qn"`/`"YYYY-YEAR"` (Art. 16). The caller
that most needs this accessor — the submit pick-list for one claim — holds the
latter. Rather than make every caller expand the quarter itself (a second copy
of a mapping `claim_lines.period_months` is documented as centralizing "the same
reason `claim_gates.is_synthetic()` is centralized"), this accessor takes either
shape and expands a claim reference period through THAT function. No new
vocabulary, no new mapping, and a malformed value still refuses 422
`invalid_period` — the code both existing period validators already raise.
"""

from __future__ import annotations

import re

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.transport.fuel_transaction import FuelTransaction
from app.services import issuer, modules
from app.services.transport.claim import validate_ref_period
from app.services.transport.claim_lines import period_months

# `"YYYY-MM"` — the model's own `period` column shape (`receipt_control`'s
# identical regex; re-declared rather than imported so this module does not
# depend on the receipt-control surface for a shape it owns a filter on).
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Pagination bounds, mirrored from the LARGE-list precedents in this codebase
# (`api/routes/expenses.py`, `api/routes/email.py`: default 100, max 500) rather
# than the 20/100 of `api/routes/invoices.py`: one entity's quarter of fuel-card
# lines is hundreds of rows, and the pick-list this feeds must be able to show a
# whole claim's scope without paging through it a screen at a time.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


def resolve_period_months(period: str) -> list[str]:
    """`"YYYY-MM"` -> itself; a claim reference period -> the months it spans.

    Public because the route's own docstring documents the accepted shapes and
    a future analytics slice will need the identical resolution; the quarter/
    year expansion itself is NOT re-implemented here — it delegates to
    `claim_lines.period_months`, the one place that mapping lives.

    Refuses 422 `invalid_period` for anything else: fails CLOSED, because
    silently returning an empty list for a typo'd period would look exactly
    like "this entity had no fuel this quarter", which is a materially
    different (and dangerous) answer for an operator building a claim.
    """
    if _MONTH_RE.match(period):
        return [period]
    try:
        # The canonical claim-period validator, reused rather than re-expressed:
        # `period_months` itself is deliberately trusting (its docstring says the
        # shape is "enforced at claim creation + the DB CHECK constraint"), so
        # calling it on an unvalidated string would happily read "2026-13" as Q3.
        validate_ref_period(period)
    except ValidationError as exc:
        raise ValidationError(
            f"'{period}' is not a valid period — use YYYY-MM, YYYY-Q1..YYYY-Q4 or YYYY-YEAR",
            code="invalid_period",
        ) from exc
    return period_months(period)


def _filtered(
    org_id: str,
    *,
    entity_id: str,
    months: list[str],
    supplier: str | None,
    country: str | None,
) -> list:
    """The ONE filter set both the page query and the count query are built
    from — so `total` can never describe a different set than `items`."""
    where = [
        FuelTransaction.org_id == org_id,
        FuelTransaction.entity_id == entity_id,
        FuelTransaction.period.in_(months),
    ]
    if supplier:
        where.append(FuelTransaction.supplier == supplier)
    if country:
        where.append(FuelTransaction.country == country.upper())
    return where


async def list_fuel_transactions(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    period: str,
    supplier: str | None = None,
    country: str | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[FuelTransaction], int]:
    """One (entity, period) page of validated fuel transactions, plus the total
    matching the SAME filters — the route-facing read (WO-79).

    Gate order, the transport-wide entry-point pattern, fails CLOSED:

    1. `transport` module entitlement (`modules.is_enabled`, a bool — never
       `modules.require_enabled`, which raises `fastapi.HTTPException`, the
       wrong shape for a service). ADR-P3 rule 3: no transport service trusts
       its caller to have checked the entitlement.
    2. The entity, fetched ORG-SCOPED through the AP/AR core's own service
       (`issuer.get_by_id`, never a raw cross-domain join — the same reasoning
       as `fuel_ingest.ingest_transaction`/`claim.get_or_create_claim`). A
       cross-tenant `entity_id` is indistinguishable from an unknown one:
       404 `entity_not_found`, never 403 (master-context §4.4). This runs
       BEFORE any transaction row is touched.
    3. `period` resolution (422 `invalid_period` — see `resolve_period_months`).

    `country` is matched upper-cased because `fuel_ingest` upper-cases the
    column on write; `supplier` is matched EXACTLY, the same convention
    `invoice_match.registered_invoices` uses for the fuel-card network code
    (the supplier set is admin-curated master data, not a fuzzy-matched name).

    Ordering is `(period, supplier, txn_date, line_seq)` — deterministic and
    stable across pages, and it groups a statement's lines together, which is
    how an operator reads them. `line_seq` is the tiebreaker precisely because
    it is the natural key's own within-file position (WO-50), so two rows can
    never compare equal.

    Returns `(rows, total)` rather than a page object: shaping the wire
    response is the route's job (engineering-rules §3), and the count is what
    the route needs to fill `FuelTransactionListOut.total` without re-deriving
    the filter set.
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    entity = await issuer.get_by_id(db, org_id, entity_id)
    if entity is None:
        raise NotFoundError("Entity not found", code="entity_not_found")

    months = resolve_period_months(period)
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    where = _filtered(
        org_id, entity_id=entity_id, months=months, supplier=supplier, country=country
    )

    total = await db.scalar(select(func.count(FuelTransaction.id)).where(*where))
    stmt: Select = (
        select(FuelTransaction)
        .where(*where)
        .order_by(
            FuelTransaction.period,
            FuelTransaction.supplier,
            FuelTransaction.txn_date,
            FuelTransaction.line_seq,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list(await db.scalars(stmt))
    return rows, int(total or 0)
