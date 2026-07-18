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

from app.schemas.invoice import InvoiceCreate, LineItemIn, ParsedInvoiceDraft

_LINE_COLS = {"description", "category", "quantity", "unit_price", "amount", "tax_rate"}


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


def _parse_json(content: bytes, filename: str, warnings: list[str]) -> InvoiceCreate:
    data = json.loads(content.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON invoice must be an object")

    lines = [_line_from(li) for li in data.get("line_items", []) if isinstance(li, dict)]
    issue = _to_date(data.get("issue_date")) or date.today()
    if data.get("issue_date") and _to_date(data.get("issue_date")) is None:
        warnings.append("Could not parse issue_date; defaulted to today")

    return InvoiceCreate(
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


def _parse_csv(content: bytes, filename: str, warnings: list[str]) -> InvoiceCreate:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row")

    fields = {(f or "").strip().lower() for f in reader.fieldnames}
    if not (fields & _LINE_COLS):
        raise ValueError(
            "CSV needs at least one of: " + ", ".join(sorted(_LINE_COLS))
        )

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

    return InvoiceCreate(
        vendor_name=vendor_name or None,
        invoice_number=str(first.get("invoice_number") or _stem(filename)),
        issue_date=issue,
        due_date=_to_date(first.get("due_date")),
        currency=(first.get("currency") or "EUR")[:3].upper(),
        source_filename=filename,
        line_items=lines,
    )


def _stem(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0][:120] or "INV"


def parse_invoice_file(filename: str, content: bytes) -> ParsedInvoiceDraft:
    """Parse `content` into a draft invoice. Raises ValueError on bad input."""
    warnings: list[str] = []
    lower = filename.lower()
    if lower.endswith(".pdf"):
        # PDF path (text layer → OCR fallback) returns its own draft + warnings.
        from app.services import pdf_ocr

        try:
            return pdf_ocr.parse_pdf(filename, content)
        except pdf_ocr.OcrUnavailable as exc:
            raise ValueError(
                "PDF support is not installed on the server "
                f"(pdfplumber/pypdfium2/pytesseract + tesseract binary): {exc}"
            )
    if lower.endswith(".json"):
        draft = _parse_json(content, filename, warnings)
    elif lower.endswith(".csv"):
        draft = _parse_csv(content, filename, warnings)
    else:
        raise ValueError("Unsupported file type. Upload a .pdf, .csv, or .json file.")

    if not draft.line_items:
        warnings.append("No line items were found in the file")
    return ParsedInvoiceDraft(draft=draft, warnings=warnings)
