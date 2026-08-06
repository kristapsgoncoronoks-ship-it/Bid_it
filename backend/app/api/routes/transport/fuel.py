"""Transport routes slice 3 — the fuel-transaction READ surface (WO-79).

One thin controller over `app.services.transport.fuel.list_fuel_transactions`:
the first route in this codebase that returns a `fuel_transactions` row. Until
now the table was reachable only through DERIVED surfaces (claim lines, the
receipt-control grid's per-slot counts), which is why
`tests/test_tenancy_parity.py` carried it as an EXEMPT row and why the SPA's
submit dialog had to ask an operator to type a fuel-transaction UUID by hand
(WO-78's own recorded limitation). Both are closed by this module existing.

Thin controller ONLY (engineering-rules §3): parse → structural permission gate
→ call the already-gated service → shape the response. The refusals a client
sees (`module_not_enabled`, `entity_not_found`, `invalid_period`) are raised BY
THE SERVICE as `app.core.errors.AppError` and rendered by the one `app.main`
handler as `{"detail", "code"}` — this module maps nothing, so the wire
vocabulary cannot drift from the service layer (master-context §4.20).

AUTHORIZATION (ADR-0024, structural): router-level `VAT_READ`, and the choice is
deliberate. `app/core/authz.py` also carries `TRANSPORT_READ` ("fuel/toll
analytics, excise (advisory)") — but this surface is not analytics: these rows
ARE the evidence base of a claim. `claim_lines.build_claim_lines` reads exactly
this set to materialize the lines that `GET /transport/claims/{id}/lines`
already serves under `VAT_READ`, and the consumer of this route is the SUBMIT
pick-list on the claim workspace. `TRANSPORT_READ` stays reserved for the
derived analytics/excise slices (`recovery.py`/`excise.py`/`overcharges.py`),
which have no backing service yet. The decision changes no effective access:
`ROLE_PERMISSIONS` grants BOTH permissions to exactly the same six roles and
denies both to APPROVER and EMPLOYEE. No permission member is added (§10).

READ-ONLY BY CONSTRUCTION: there is no POST/PATCH/DELETE here and there is not
meant to be. Ingestion enforces the natural-key idempotency, the money
quantization and the `product_group` derivation inside `fuel_ingest`/
`statement_ingest`; a route-tier writer would duplicate or bypass all three.
Nothing mutates, so nothing is audited here and no `db.commit()` is issued.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.transport.fuel_transaction import FuelTransaction
from app.schemas.transport_fuel import FuelTransactionListOut, FuelTransactionOut
from app.services.transport import fuel as fuel_svc

# Structural authorization (ADR-0024): router-level VAT_READ. Every route in
# this module is a read, so there is no stricter per-route override.
router = APIRouter(
    prefix="/transport/fuel-transactions",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.VAT_READ))],
)


def _txn_out(txn: FuelTransaction) -> FuelTransactionOut:
    return FuelTransactionOut(
        id=txn.id,
        entity_id=txn.entity_id,
        supplier=txn.supplier,
        period=txn.period,
        line_seq=txn.line_seq,
        country=txn.country,
        vehicle_ref=txn.vehicle_ref,
        txn_date=txn.txn_date,
        txn_time=txn.txn_time,
        station=txn.station,
        product=txn.product,
        product_group=txn.product_group,
        qty=txn.qty,
        currency=txn.currency,
        net_local=txn.net_local,
        vat_local=txn.vat_local,
        gross_local=txn.gross_local,
        net_eur=txn.net_eur,
        vat_eur=txn.vat_eur,
        net_eur_eff=txn.net_eur_eff,
        invoice_ref=txn.invoice_ref,
        provenance_note=txn.provenance_note,
        invoice_id=txn.invoice_id,
        fx_rate=txn.fx_rate,
        fx_ecb_rate=txn.fx_ecb_rate,
        fx_ecb_date=txn.fx_ecb_date,
        fx_source=txn.fx_source,
        created_at=txn.created_at,
    )


@router.get("", response_model=FuelTransactionListOut)
async def list_fuel_transactions(
    current: CurrentUser,
    db: DbSession,
    entity_id: str = Query(description="The claimant's own legal entity (issuer profile id)"),
    period: str = Query(
        description="YYYY-MM, or a claim reference period YYYY-Q1..YYYY-Q4 / YYYY-YEAR "
        "(expanded to its months by the service)"
    ),
    supplier: str | None = Query(default=None, description="fuel-card / toll-network code"),
    country: str | None = Query(
        default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country of SUPPLY"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=fuel_svc.DEFAULT_PAGE_SIZE, ge=1, le=fuel_svc.MAX_PAGE_SIZE),
):
    """One (entity, period) page of validated fuel transactions.

    `entity_id` and `period` are required because an unbounded dump of a
    tenant's whole fuel history is not a surface any consumer needs — the claim
    workspace asks for exactly one claim's grain, and the receipt-control /
    analytics slices ask per period. `supplier` and `country` narrow it further;
    `country` matters because `claim_lines.build_claim_lines` scopes a claim's
    transactions by country of supply, so a pick-list that ignored it would
    offer rows the claim provably excludes.

    Amounts (and `qty`) cross the wire as exact decimal STRINGS (§4.9).
    """
    rows, total = await fuel_svc.list_fuel_transactions(
        db,
        current.org_id,
        entity_id=entity_id,
        period=period,
        supplier=supplier,
        country=country,
        page=page,
        page_size=page_size,
    )
    return FuelTransactionListOut(
        items=[_txn_out(t) for t in rows], total=total, page=page, page_size=page_size
    )
