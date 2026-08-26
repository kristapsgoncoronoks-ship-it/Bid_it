"""Supplier agreed prices — cost control (WO-G phase 2).

The tenant records what a unit price SHOULD be (per supplier × item, with a
validity window); this module answers three questions with it:

- **What is the agreed price for this line?** `active_price` — the row whose
  window covers the date, latest `valid_from` winning (the most recent
  agreement is the one in force).
- **Does this invoice overcharge?** `check_invoice` — one advisory
  ValidationFinding per line priced above agreement (code
  `agreed_price_exceeded`, registered in the ONE rule registry). The AP
  submit gate additionally REFUSES such an invoice when the org opted into
  `overcharge_block_enabled` — advisory by default, block by choice
  (design §2, open question 2 resolved).
- **What has been overcharged already?** `overcharge_worklist` — the read
  model over captured lines vs agreements: each exceeding line with its
  per-unit delta and the quantity-weighted overcharge total. Same
  single-currency discipline (C1.7) as the phase-1 read models.

The item identity is phase 1's: `lower(trim(description))`. No item master.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreed_price import SupplierAgreedPrice
from app.models.invoice import Invoice, LineItem
from app.models.vendor import Vendor
from app.schemas.validation import ValidationFinding

_CENT = Decimal("0.01")
_PCT = Decimal("0.1")


class AgreedPriceError(Exception):
    """Base for agreed-price failures the route maps to HTTP 400."""


class NotFoundError(AgreedPriceError):
    """Unknown (or other-tenant — indistinguishable, §4.4) id."""


def normalize_item(item: str) -> str:
    return item.strip().lower()


def _money(v: Decimal) -> str:
    return str(v.quantize(_CENT, rounding=ROUND_HALF_UP))


async def upsert(
    db: AsyncSession,
    org_id: str,
    *,
    vendor_id: str,
    item: str,
    agreed_price: Decimal,
    currency: str = "EUR",
    valid_from: date | None = None,
    valid_to: date | None = None,
    note: str | None = None,
    created_by: str | None = None,
) -> SupplierAgreedPrice:
    """Create or update the entry for (supplier, item, currency, window
    start). Editing the same window start updates in place — the natural key
    keeps 'set the price again' from silently duplicating the list."""
    vendor = await db.scalar(
        select(Vendor.id).where(Vendor.org_id == org_id, Vendor.id == vendor_id)
    )
    if vendor is None:
        raise NotFoundError("supplier not found")
    key = normalize_item(item)
    if not key:
        raise AgreedPriceError("item is required")
    if agreed_price <= 0:
        raise AgreedPriceError("agreed price must be positive")
    valid_from = valid_from or date.today()
    if valid_to is not None and valid_to < valid_from:
        raise AgreedPriceError("valid_to is before valid_from")
    cur = currency.upper()

    row = await db.scalar(
        select(SupplierAgreedPrice).where(
            SupplierAgreedPrice.org_id == org_id,
            SupplierAgreedPrice.vendor_id == vendor_id,
            SupplierAgreedPrice.item == key,
            SupplierAgreedPrice.currency == cur,
            SupplierAgreedPrice.valid_from == valid_from,
        )
    )
    if row is None:
        row = SupplierAgreedPrice(
            org_id=org_id,
            vendor_id=vendor_id,
            item=key,
            currency=cur,
            agreed_price=agreed_price,
            valid_from=valid_from,
            valid_to=valid_to,
            note=note,
            created_by=created_by,
        )
        db.add(row)
    else:
        row.agreed_price = agreed_price
        row.valid_to = valid_to
        row.note = note
    await db.flush()
    return row


async def remove(db: AsyncSession, org_id: str, price_id: str) -> SupplierAgreedPrice:
    row = await db.scalar(
        select(SupplierAgreedPrice).where(
            SupplierAgreedPrice.org_id == org_id, SupplierAgreedPrice.id == price_id
        )
    )
    if row is None:
        raise NotFoundError("agreed price not found")
    await db.delete(row)
    await db.flush()
    return row


async def list_prices(db: AsyncSession, org_id: str, *, vendor_id: str | None = None) -> list[dict]:
    stmt = (
        select(SupplierAgreedPrice, Vendor.name)
        .join(Vendor, SupplierAgreedPrice.vendor_id == Vendor.id)
        .where(SupplierAgreedPrice.org_id == org_id)
        .order_by(Vendor.name, SupplierAgreedPrice.item, SupplierAgreedPrice.valid_from.desc())
    )
    if vendor_id is not None:
        stmt = stmt.where(SupplierAgreedPrice.vendor_id == vendor_id)
    return [
        {
            "id": r.id,
            "vendor_id": r.vendor_id,
            "vendor_name": name,
            "item": r.item,
            "currency": r.currency,
            "agreed_price": _money(r.agreed_price),
            "valid_from": r.valid_from.isoformat(),
            "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            "note": r.note,
        }
        for r, name in await db.execute(stmt)
    ]


async def active_price(
    db: AsyncSession,
    org_id: str,
    *,
    vendor_id: str,
    item: str,
    currency: str,
    on_date: date,
) -> Decimal | None:
    """The agreed price in force on `on_date`, or None. Overlapping windows
    resolve to the LATEST valid_from — the most recent agreement wins."""
    row = await db.scalar(
        select(SupplierAgreedPrice.agreed_price)
        .where(
            SupplierAgreedPrice.org_id == org_id,
            SupplierAgreedPrice.vendor_id == vendor_id,
            SupplierAgreedPrice.item == normalize_item(item),
            SupplierAgreedPrice.currency == currency.upper(),
            SupplierAgreedPrice.valid_from <= on_date,
            (SupplierAgreedPrice.valid_to.is_(None)) | (SupplierAgreedPrice.valid_to >= on_date),
        )
        .order_by(SupplierAgreedPrice.valid_from.desc())
        .limit(1)
    )
    return Decimal(row) if row is not None else None


async def check_invoice(
    db: AsyncSession, invoice: Invoice, today: date | None = None
) -> list[ValidationFinding]:
    """One finding per line whose unit price exceeds the agreed price in
    force on the invoice's issue date. Advisory by registry policy; the
    submit gate escalates it to a refusal only when the org opted in."""
    from app.services.validation import rule  # local import — validation imports us

    if invoice.vendor_id is None:
        return []
    on = invoice.issue_date or today or date.today()
    tol = rule("agreed_price_exceeded").tolerance
    out: list[ValidationFinding] = []
    for li in invoice.line_items:
        if not li.quantity or li.quantity <= 0 or not li.unit_price or li.unit_price <= 0:
            continue
        agreed = await active_price(
            db,
            invoice.org_id,
            vendor_id=invoice.vendor_id,
            item=li.description or "",
            currency=invoice.currency or "EUR",
            on_date=on,
        )
        if agreed is None:
            continue
        unit = Decimal(li.unit_price)
        if unit - agreed > tol:
            delta = unit - agreed
            pct = delta / agreed * 100
            out.append(
                ValidationFinding(
                    severity="warning",
                    code="agreed_price_exceeded",
                    message=(
                        f"Line '{(li.description or '')[:40]}': unit price {_money(unit)} exceeds "
                        f"the agreed {_money(agreed)} "
                        f"(+{_money(delta)}, +{pct.quantize(_PCT, rounding=ROUND_HALF_UP)}%)"
                    ),
                )
            )
    return out


async def overcharge_worklist(db: AsyncSession, org_id: str, *, window_days: int = 365) -> dict:
    """Every captured line priced above the agreement in force on its
    invoice date, with the quantity-weighted overcharge. Loads the (small)
    agreed list first, then only the lines for those supplier × item pairs
    — the list is the filter, so a tenant without agreements costs one
    query."""
    agreed_rows = list(
        await db.scalars(select(SupplierAgreedPrice).where(SupplierAgreedPrice.org_id == org_id))
    )
    if not agreed_rows:
        return {"window_days": window_days, "total_overcharge": "0.00", "rows": []}

    by_vendor: dict[str, set[str]] = {}
    for a in agreed_rows:
        by_vendor.setdefault(a.vendor_id, set()).add(a.item)

    start = date.today() - timedelta(days=window_days)
    item_key = func.lower(func.trim(LineItem.description))
    stmt = (
        select(
            Invoice.id,
            Invoice.invoice_number,
            Invoice.issue_date,
            Invoice.currency,
            Invoice.vendor_id,
            Vendor.name,
            item_key.label("item"),
            LineItem.quantity,
            LineItem.unit_price,
        )
        .join(Invoice, LineItem.invoice_id == Invoice.id)
        .join(Vendor, Invoice.vendor_id == Vendor.id)
        .where(
            Invoice.org_id == org_id,
            Invoice.vendor_id.in_(list(by_vendor)),
            Invoice.issue_date >= start,
            LineItem.quantity > 0,
            LineItem.unit_price > 0,
        )
        .order_by(Invoice.issue_date.desc())
    )

    def _in_force(a: SupplierAgreedPrice, on: date, currency: str) -> bool:
        return (
            a.currency == (currency or "EUR").upper()
            and a.valid_from <= on
            and (a.valid_to is None or a.valid_to >= on)
        )

    rows: list[dict] = []
    total = Decimal("0")
    for inv_id, number, issue, cur, vid, vname, item, qty, unit in await db.execute(stmt):
        if item not in by_vendor.get(vid, set()):
            continue
        candidates = [
            a
            for a in agreed_rows
            if a.vendor_id == vid and a.item == item and _in_force(a, issue, cur)
        ]
        if not candidates:
            continue
        agreed = max(candidates, key=lambda a: a.valid_from).agreed_price
        unit = Decimal(unit)
        if unit <= agreed:
            continue
        over = (unit - agreed) * Decimal(qty)
        total += over
        rows.append(
            {
                "invoice_id": inv_id,
                "invoice_number": number,
                "issue_date": issue.isoformat(),
                "currency": cur,
                "vendor_id": vid,
                "vendor_name": vname,
                "item": item,
                "quantity": str(qty),
                "unit_price": _money(unit),
                "agreed_price": _money(agreed),
                "delta_per_unit": _money(unit - agreed),
                "overcharge": _money(over),
            }
        )
    return {
        "window_days": window_days,
        "total_overcharge": _money(total),
        "rows": rows,
    }
