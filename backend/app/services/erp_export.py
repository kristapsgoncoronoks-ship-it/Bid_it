"""Accounting / ERP exports of the received-invoice ledger (arch Phase 3.10).

Read-only over `invoices` (+ vendor name) for a period, rendered into files a
bookkeeper reviews before importing into an accounting package. Generic v2 labels
stored total_eur as Gross EUR and discloses FX provenance/completeness. Branded
adapters are unavailable at both the API and service boundaries until a reviewed
importer contract and account/tax mappings exist. No guessed renderer is retained.

Every cell is formula-injection-safe (a leading =, +, -, @, tab or CR is
neutralised) because these files are opened in Excel/Sheets. Invents no figure —
amounts come straight from the validated invoice record.

DATEV (Buchungsstapel/EXTF) is intentionally NOT here yet: it requires the German
SKR account framework + the EXTF header spec and per-account tax keys, so it is a
separate, config-gated build rather than a guessed template.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.csv_safety import sanitize_cell as _safe
from app.core.errors import ConflictError
from app.models.invoice import Invoice

# Capability metadata is shared by API discovery and service enforcement.
# No provider is enabled until its reviewed adapter is implemented separately.
FORMATS = ("generic", "xero", "quickbooks")
FORMAT_CATALOGUE = (
    {
        "key": "generic",
        "action_id": "ACC01",
        "label": "Generic accounting CSV",
        "enabled": True,
        "contract": "generic-v2",
        "reason": None,
        "setup_steps": [],
    },
    {
        "key": "xero",
        "action_id": "ACC02",
        "label": "Xero — unconfigured",
        "enabled": False,
        "contract": None,
        "reason": "Xero requires a reviewed product/region import template and account, tax, tracking and currency mappings.",
        "setup_steps": [
            "Ask your administrator and accountant to select the Xero organisation, region and exact import template.",
            "Review account, tax, tracking and currency mappings with synthetic bills in a sandbox before enabling this adapter.",
        ],
    },
    {
        "key": "quickbooks",
        "action_id": "ACC03",
        "label": "QuickBooks — unconfigured",
        "enabled": False,
        "contract": None,
        "reason": "QuickBooks requires a reviewed edition/region import contract and supplier, account and tax-code mappings.",
        "setup_steps": [
            "Ask your administrator and accountant to confirm an eligible QuickBooks Online edition, region and import template.",
            "Review supplier, account and tax-code mappings, batch limits and unsupported credits or discounts using synthetic sandbox bills before enabling this adapter.",
        ],
    },
)


def require_configured(fmt: str) -> None:
    """Fail before reading rows or generating bytes, including internal callers."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown export format '{fmt}'")
    descriptor = next(item for item in FORMAT_CATALOGUE if item["key"] == fmt)
    if not descriptor["enabled"]:
        raise ConflictError(str(descriptor["reason"]), code="export_adapter_unconfigured")


@dataclass
class LedgerRow:
    supplier: str
    number: str
    issue_date: date
    due_date: date | None
    currency: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    total_eur: Decimal | None
    cost_center: str | None
    department: str | None
    project: str | None
    vehicle: str | None
    property_ref: str | None
    status: str
    fx_source: str | None = None
    fx_rate: Decimal | None = None


async def ledger(
    db: AsyncSession, org_id: str, start: date | None, end: date | None
) -> list[LedgerRow]:
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.vendor))
        .where(Invoice.org_id == org_id)
        .order_by(Invoice.issue_date, Invoice.invoice_number)
    )
    if start is not None:
        stmt = stmt.where(Invoice.issue_date >= start)
    if end is not None:
        stmt = stmt.where(Invoice.issue_date <= end)
    rows = list(await db.scalars(stmt))
    return [row_of(inv) for inv in rows]


def row_of(inv: Invoice) -> LedgerRow:
    return LedgerRow(
        supplier=(inv.vendor.name if inv.vendor else ""),
        number=inv.invoice_number,
        issue_date=inv.issue_date,
        due_date=inv.due_date,
        currency=inv.currency,
        subtotal=inv.subtotal,
        tax=inv.tax_amount,
        total=inv.total,
        total_eur=inv.total_eur,
        cost_center=inv.cost_center,
        department=inv.department,
        project=inv.project,
        vehicle=inv.vehicle,
        property_ref=inv.property_ref,
        status=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
        fx_source=inv.fx_source,
        fx_rate=inv.fx_rate,
    )


# --- CSV safety --------------------------------------------------------------- #
# `_safe` is `app.core.csv_safety.sanitize_cell` imported under its original
# local name (board R1/R9 — one shared implementation, no per-file copy).


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_safe(c) for c in r])
    return buf.getvalue()


def _iso(d: date | None) -> str:
    return d.isoformat() if d else ""


# --- Format renderers --------------------------------------------------------- #


def _generic(rows: list[LedgerRow]) -> str:
    header = [
        "Date",
        "Due Date",
        "Supplier",
        "Invoice Number",
        "Currency",
        "Net",
        "VAT",
        "Gross",
        "Gross EUR",
        "Cost Center",
        "Department",
        "Project",
        "Vehicle",
        "Property",
        "Status",
        "FX Source",
        "FX Rate (currency per EUR)",
        "Conversion Status",
    ]
    return _csv(
        header,
        [
            [
                _iso(r.issue_date),
                _iso(r.due_date),
                r.supplier,
                r.number,
                r.currency,
                r.subtotal,
                r.tax,
                r.total,
                (r.total_eur if r.total_eur is not None else ""),
                r.cost_center,
                r.department,
                r.project,
                r.vehicle,
                r.property_ref,
                r.status,
                r.fx_source,
                r.fx_rate,
                "available" if r.total_eur is not None else "unavailable",
            ]
            for r in rows
        ],
    )


_RENDERERS = {"generic": _generic}


def render(fmt: str, rows: list[LedgerRow]) -> tuple[str, str]:
    """Render a configured format only; blocked formats raise ConflictError."""
    require_configured(fmt)
    return "invoices-generic-v2.csv", _RENDERERS[fmt](rows)
