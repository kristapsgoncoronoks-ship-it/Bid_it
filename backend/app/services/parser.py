"""Ingestion: turn an uploaded file into a *draft* invoice.

This is the seam where real extraction lives. Today it handles the structured
formats a finance team can already export (CSV line items, JSON). PDF/OCR is the
next adapter to slot in behind the same `parse_invoice_file` interface — it would
push originals to object storage and enqueue a background OCR job, then return the
same `ParsedInvoiceDraft`.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.schemas.invoice import FieldProvenance, InvoiceCreate, LineItemIn, ParsedInvoiceDraft

_LINE_COLS = {"description", "category", "quantity", "unit_price", "amount", "tax_rate"}


def _provenance(source: dict, draft: InvoiceCreate) -> list[FieldProvenance]:
    """Per-field capture provenance for the top-level fields (Slice 5f). `source`
    is the raw provided data; the status says whether each field was read from it,
    filled with a default, or is missing."""
    src = {str(k).strip().lower(): v for k, v in source.items()}

    def has(*keys: str) -> bool:
        return any(src.get(k) not in (None, "") for k in keys)

    def status(present: bool, has_default: bool) -> str:
        return "extracted" if present else ("defaulted" if has_default else "missing")

    return [
        FieldProvenance(
            field="invoice_number",
            value=draft.invoice_number,
            status=status(has("invoice_number"), True),  # defaults to the filename stem
        ),
        FieldProvenance(
            field="vendor_name",
            value=draft.vendor_name,
            status=status(has("vendor_name", "vendor"), False),  # no default
        ),
        FieldProvenance(
            field="issue_date",
            value=draft.issue_date.isoformat(),
            status=status(_to_date(src.get("issue_date")) is not None, True),  # defaults to today
        ),
        FieldProvenance(
            field="due_date",
            value=draft.due_date.isoformat() if draft.due_date else None,
            status=status(_to_date(src.get("due_date")) is not None, False),
        ),
        FieldProvenance(
            field="currency",
            value=draft.currency,
            status=status(has("currency"), True),  # defaults to EUR
        ),
    ]


def _to_decimal(value, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _to_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _line_from(raw: dict) -> LineItemIn:
    qty = _to_decimal(raw.get("quantity"), "1")
    unit = _to_decimal(raw.get("unit_price"), "0")
    amount = raw.get("amount")
    amount_dec = _to_decimal(amount) if amount not in (None, "") else (qty * unit)
    return LineItemIn(
        description=str(raw.get("description") or "Item").strip()[:500],
        category=str(raw.get("category") or "uncategorized").strip()[:80],
        quantity=qty,
        unit_price=unit,
        amount=amount_dec,
        tax_rate=_to_decimal(raw.get("tax_rate"), "0"),
    )


def _parse_json(
    content: bytes, filename: str, warnings: list[str]
) -> tuple[InvoiceCreate, list[FieldProvenance]]:
    data = json.loads(content.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON invoice must be an object")

    lines = [_line_from(li) for li in data.get("line_items", []) if isinstance(li, dict)]
    issue = _to_date(data.get("issue_date")) or date.today()
    if data.get("issue_date") and _to_date(data.get("issue_date")) is None:
        warnings.append("Could not parse issue_date; defaulted to today")

    draft = InvoiceCreate(
        vendor_name=(data.get("vendor_name") or data.get("vendor") or None),
        vendor_id=data.get("vendor_id"),
        invoice_number=str(data.get("invoice_number") or _stem(filename)),
        issue_date=issue,
        due_date=_to_date(data.get("due_date")),
        currency=(data.get("currency") or "EUR")[:3].upper(),
        notes=data.get("notes"),
        source_filename=filename,
        line_items=lines,
    )
    return draft, _provenance(data, draft)


def _parse_csv(
    content: bytes, filename: str, warnings: list[str]
) -> tuple[InvoiceCreate, list[FieldProvenance]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row")

    fields = {(f or "").strip().lower() for f in reader.fieldnames}
    if not (fields & _LINE_COLS):
        raise ValueError("CSV needs at least one of: " + ", ".join(sorted(_LINE_COLS)))

    rows = list(reader)
    if not rows:
        raise ValueError("CSV has a header but no rows")

    # Invoice-level metadata may be repeated on the first row.
    first = {k.strip().lower(): v for k, v in rows[0].items()}
    lines = [_line_from({k.strip().lower(): v for k, v in r.items()}) for r in rows]

    issue = _to_date(first.get("issue_date")) or date.today()
    if not first.get("issue_date"):
        warnings.append("No issue_date column; defaulted to today")

    vendor_name = first.get("vendor") or first.get("vendor_name")
    if not vendor_name:
        warnings.append("No vendor column; set the vendor before saving")

    draft = InvoiceCreate(
        vendor_name=vendor_name or None,
        invoice_number=str(first.get("invoice_number") or _stem(filename)),
        issue_date=issue,
        due_date=_to_date(first.get("due_date")),
        currency=(first.get("currency") or "EUR")[:3].upper(),
        source_filename=filename,
        line_items=lines,
    )
    return draft, _provenance(first, draft)


def _stem(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0][:120] or "INV"


def parse_invoice_file(filename: str, content: bytes) -> ParsedInvoiceDraft:
    """Parse `content` into a draft invoice. Raises ValueError on bad input.

    This is the single choke point every extraction path funnels through, so it
    is also where we record the deterministic-capture-rate metric (by method).
    """
    from app.core import metrics

    try:
        result = _dispatch_parse(filename, content)
    except ValueError:
        metrics.record_parse("failed")
        raise
    metrics.record_parse(result.method)
    return result


def _dispatch_parse(filename: str, content: bytes) -> ParsedInvoiceDraft:
    """Route the file through the pluggable provider registry (deterministic-first).
    Each provider wraps a real backend (PDF text/OCR, e-invoice XML, CSV, JSON) and
    returns per-field captures with honest confidence; see `extraction_provider`.
    A future OCR/document-AI vendor registers there without changing this seam."""
    from app.services import extraction_provider

    return extraction_provider.to_draft(extraction_provider.run(filename, content))
