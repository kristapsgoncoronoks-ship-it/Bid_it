"""Response schemas for the transport fuel-transaction read route (WO-79).

Field-for-field from `app/models/transport/fuel_transaction.py` — the naming and
the money convention of `transport_claim.py`: every amount is typed `Decimal`, so
pydantic v2 serializes it as a JSON STRING and no float ever appears in a money
path (master-context §4.9). Nothing here computes, rounds or invents a figure;
`fuel_ingest.ingest_transaction` already quantized every monetary column via
`app.core.money.q2` before the row was stored.

`qty` is a `Decimal` too but is DELIBERATELY NOT a money value — it is the €/L
denominator, stored `Numeric(14,3)` and explicitly carved out of the quantization
rule by the model's own docstring (and by §4.9 itself). It crosses the wire as an
exact string for the same reason the amounts do: three decimal places of litres
survive a JSON round-trip only if nothing turns them into a double.

The four FX-provenance columns are carried verbatim. §4.15's whole point is that a
converted amount is meaningless without the rate, the ECB reference and the
source that produced it; a read surface that dropped them would hand a consumer a
number it cannot audit.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class FuelTransactionOut(BaseModel):
    id: str
    entity_id: str
    supplier: str
    # "YYYY-MM" — the accounting month, half the natural key (WO-50).
    period: str
    # 1-based position within the SOURCE FILE for (entity, supplier, period);
    # the other half of the natural key, and this listing's stable tiebreaker.
    line_seq: int

    # ISO 3166-1 alpha-2 of the country of SUPPLY = the VAT jurisdiction.
    country: str
    vehicle_ref: str
    txn_date: date
    txn_time: str
    station: str
    product: str
    # One of `fuel_transaction.PRODUCT_GROUPS`, derived by
    # `product_group.derive_product_group()` — never caller-supplied.
    product_group: str

    # Litres / units. NOT money — see the module docstring.
    qty: Decimal
    currency: str

    net_local: Decimal
    vat_local: Decimal
    gross_local: Decimal
    net_eur: Decimal
    vat_eur: Decimal
    # Effective net after ALL rebate layers, including off-invoice ones.
    net_eur_eff: Decimal

    # The RAW matching reference off the source document — nullable by design
    # (often unresolved at ingestion). This is NOT an AP invoice number: the
    # resolved number lives on `vat_claim_lines.invoice_ref` after
    # `invoice_match.resolve_invoice_ref` runs.
    invoice_ref: str | None = None
    provenance_note: str | None = None
    # The RESOLVED AP invoice — NULL until a future note-matching service sets
    # it; no code populates it today (the model's own docstring).
    invoice_id: str | None = None

    # FX provenance quadruple (§4.15). `fx_source` ∈ {eur, stated, ecb, unknown}.
    fx_rate: Decimal | None = None
    fx_ecb_rate: Decimal | None = None
    fx_ecb_date: date | None = None
    fx_source: str | None = None

    created_at: datetime


class FuelTransactionListOut(BaseModel):
    """The `InvoiceListOut` shape (`app/schemas/invoice.py`) — this codebase's
    established paginated-list envelope, reused rather than re-invented."""

    items: list[FuelTransactionOut]
    total: int
    page: int
    page_size: int
