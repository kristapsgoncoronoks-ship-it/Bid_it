"""Accounting / ERP exports of the received-invoice ledger (arch Phase 3.10).

Read-only over `invoices` (+ vendor name) for a period, rendered into files a
bookkeeper imports into their accounting package. NET EUR basis is stated on the
generic export; branded exports follow each package's documented bill-import
columns as starting templates (the importer still maps accounts/tax to the
tenant's own chart).

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
from app.models.invoice import Invoice

# Default chart mappings (constants for now; can become per-org settings later).
DEFAULT_XERO_ACCOUNT = "400"  # e.g. a general expenses/purchases account
DEFAULT_XERO_TAX_TYPE = "NONE"  # importer remaps to the org's tax rate
DEFAULT_QBO_ACCOUNT = "Accounts Payable"

# Formats the export hub offers.
FORMATS = ("generic", "xero", "quickbooks")


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
    return [
        LedgerRow(
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
        )
        for inv in rows
    ]


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
        "Net EUR",
        "Cost Center",
        "Department",
        "Project",
        "Vehicle",
        "Property",
        "Status",
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
            ]
            for r in rows
        ],
    )


def _xero(rows: list[LedgerRow]) -> str:
    # Xero "Bills" import columns (* = required). One line per bill at NET; the
    # importer applies tax via TaxType and posts to AccountCode.
    header = [
        "*ContactName",
        "*InvoiceNumber",
        "*InvoiceDate",
        "*DueDate",
        "*Description",
        "*Quantity",
        "*UnitAmount",
        "*AccountCode",
        "*TaxType",
        "Currency",
        "TrackingName1",
        "TrackingOption1",
    ]
    return _csv(
        header,
        [
            [
                r.supplier,
                r.number,
                _iso(r.issue_date),
                _iso(r.due_date),
                f"Invoice {r.number}",
                "1",
                r.subtotal,
                DEFAULT_XERO_ACCOUNT,
                DEFAULT_XERO_TAX_TYPE,
                r.currency,
                ("Cost Center" if r.cost_center else ""),
                (r.cost_center or ""),
            ]
            for r in rows
        ],
    )


def _quickbooks(rows: list[LedgerRow]) -> str:
    # QuickBooks Online "Bills" import columns (gross amount + memo).
    header = [
        "Supplier",
        "Bill No",
        "Bill Date",
        "Due Date",
        "Account",
        "Amount",
        "Currency",
        "Memo",
    ]
    return _csv(
        header,
        [
            [
                r.supplier,
                r.number,
                _iso(r.issue_date),
                _iso(r.due_date),
                DEFAULT_QBO_ACCOUNT,
                r.total,
                r.currency,
                f"Invoice {r.number}",
            ]
            for r in rows
        ],
    )


_RENDERERS = {"generic": _generic, "xero": _xero, "quickbooks": _quickbooks}


def render(fmt: str, rows: list[LedgerRow]) -> tuple[str, str]:
    """Return (filename, csv_text) for the format. Raises ValueError on unknown."""
    if fmt not in _RENDERERS:
        raise ValueError(f"unknown export format '{fmt}'")
    return f"invoices-{fmt}.csv", _RENDERERS[fmt](rows)
