"""Advisory receipt OCR → suggested expense-item fields (a human still confirms).

Reuses the `pdf_ocr` engine (text-layer-first, Tesseract fallback) used for bank
statements. It NEVER writes anything — it reads a receipt image/PDF and returns a
best-effort suggestion (merchant, date, amount, tax, currency) that the employee
applies or edits when adding an expense. Same advisory, human-confirmed pattern as
the bank-statement import.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services import pdf_ocr

_MONEY_RE = pdf_ocr._MONEY_RE

# "total" is deliberately last — a labelled grand-total wins over a bare number.
_TOTAL_KW = (
    "grand total",
    "amount due",
    "total due",
    "balance due",
    "to pay",
    "total amount",
    "total",
    "betrag",
    "totaal",
    "importe",
)
# Lines that look like a total but are NOT the grand total.
_TOTAL_EXCLUDE = ("subtotal", "sub total", "sub-total", "net", "vat", "tax")
_TAX_KW = ("vat", "btw", "mwst", "tva", "iva", "sales tax", "gst", "tax")
_CCY_SYMBOL = {"€": "EUR", "$": "USD", "£": "GBP"}
_CCY_CODES = ("EUR", "USD", "GBP", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK")
_DATE_TOKEN = re.compile(r"\d[\d.\-/]{5,}")


@dataclass
class ReceiptSuggestion:
    merchant: str | None
    spend_date: date | None
    amount: Decimal | None
    vat_amount: Decimal | None
    currency: str | None
    method: str  # text-layer | ocr
    text_preview: str


def _labeled_money(
    lines: list[str], keywords: tuple[str, ...], exclude: tuple[str, ...] = ()
) -> Decimal | None:
    """The largest money on the LAST line that mentions a keyword (and no excluded
    word) — totals sit near the bottom of a receipt."""
    best: Decimal | None = None
    for ln in lines:
        low = ln.lower()
        if any(k in low for k in keywords) and not any(x in low for x in exclude):
            monies = [pdf_ocr._num(m.group()) for m in _MONEY_RE.finditer(ln)]
            if monies:
                best = max(monies)  # later lines overwrite → last labelled wins
    return best


def parse_receipt_text(text: str) -> ReceiptSuggestion:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Merchant: the first meaningful line (not a date, not a bare number).
    merchant: str | None = None
    for ln in lines[:6]:
        if pdf_ocr._to_date(ln) is not None:
            continue
        if _MONEY_RE.search(ln) and len(ln) < 14:
            continue
        merchant = ln[:200]
        break

    # Date: the first parseable date token anywhere.
    spend_date: date | None = None
    for ln in lines:
        for tok in _DATE_TOKEN.findall(ln):
            d = pdf_ocr._to_date(tok)
            if d is not None:
                spend_date = d
                break
        if spend_date is not None:
            break

    # Amount: a labelled total, else the largest money figure on the receipt.
    amount = _labeled_money(lines, _TOTAL_KW, _TOTAL_EXCLUDE)
    if amount is None:
        monies = [pdf_ocr._num(m.group()) for ln in lines for m in _MONEY_RE.finditer(ln)]
        amount = max(monies) if monies else None

    vat_amount = _labeled_money(lines, _TAX_KW)

    currency: str | None = None
    for sym, code in _CCY_SYMBOL.items():
        if sym in text:
            currency = code
            break
    if currency is None:
        up = text.upper()
        for code in _CCY_CODES:
            if re.search(rf"\b{code}\b", up):
                currency = code
                break

    return ReceiptSuggestion(
        merchant=merchant,
        spend_date=spend_date,
        amount=amount,
        vat_amount=vat_amount,
        currency=currency,
        method="",
        text_preview="\n".join(lines[:12])[:800],
    )


def _text_from(filename: str, content: bytes) -> tuple[str, str]:
    """(text, method). PDFs go through the pdf_ocr engine; images are OCR'd
    directly with Tesseract."""
    lower = (filename or "").lower()
    if lower.endswith(".pdf") or content[:4] == b"%PDF":
        return pdf_ocr.extract_text(content)
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:  # pragma: no cover - env guard
        raise pdf_ocr.OcrUnavailable(str(e)) from e
    try:
        img = Image.open(io.BytesIO(content))
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError as e:  # pragma: no cover
        raise pdf_ocr.OcrUnavailable(str(e)) from e
    return pdf_ocr._reconstruct_lines(data), "ocr"


def suggest(filename: str, content: bytes) -> ReceiptSuggestion:
    """Read a receipt image/PDF → a field suggestion. Raises OcrUnavailable if the
    OCR stack is missing; ValueError if no text could be read."""
    text, method = _text_from(filename, content)
    if not text or not text.strip():
        raise ValueError("Could not read any text from the receipt.")
    s = parse_receipt_text(text)
    s.method = method
    return s
