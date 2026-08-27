"""PDF ingestion with OCR fallback.

Strategy (deterministic-first, OCR only when needed):
  1. Try the PDF's embedded text layer (pdfplumber) — exact, fast, no OCR.
  2. If the page has little/no text (a scanned or image-only PDF), rasterise
     each page with pypdfium2 and run Tesseract OCR.
  3. Run a heuristic invoice parser over the extracted text to produce a
     *draft* invoice (vendor, number, date, line items, inferred VAT rate).

Everything here is best-effort and advisory: the result is a draft a human
confirms — exactly like the CSV/JSON path. The heavy libraries are imported
lazily so the rest of the app runs without them installed.

System dependency: the Tesseract binary (`tesseract-ocr`) must be on PATH for
the OCR fallback. The text-layer path needs only Python packages.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.schemas.invoice import InvoiceCreate, LineItemIn, ParsedInvoiceDraft

# Render at this DPI for OCR; 300 is the accuracy/speed sweet spot for Tesseract.
_OCR_DPI = 300
# Below this many characters of embedded text, assume the page is scanned.
_TEXT_LAYER_MIN_CHARS = 25
# Hard cap on pages we rasterise/OCR — bounds CPU/memory against a maliciously
# huge or decompression-bomb PDF (an invoice is a handful of pages, not 10,000).
_MAX_OCR_PAGES = 50

# A monetary token: any number ending in a 2-digit decimal group, either
# separator, optional sign/parentheses (credits) and thousands separators.
# e.g. 60.00  1,234.56  1.234,56  -12.00  (9.90)
_MONEY_RE = re.compile(r"[-(]?\d[\d.,]*[.,]\d{2}\)?")
# Legacy 2-decimal amount (kept for callers that want the simplest signal).
_AMOUNT_RE = _MONEY_RE

# Dates and times — masked out before money parsing so a transaction date like
# 01.06.2026 is never mistaken for the amount 01.06.
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}[./ ][A-Za-z]{3,9}[./ ]\d{2,4})\b"
)
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_LEADING_DATE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b")

# A row is a summary/total row (not a line item) when, after stripping numbers,
# dates and punctuation, the remaining label is one of these.
_SUMMARY_LABELS = (
    "total",
    "subtotal",
    "sub total",
    "grand total",
    "net total",
    "gross total",
    "total due",
    "total net",
    "total gross",
    "amount due",
    "balance",
    "balance due",
    "vat",
    "tax",
    "to pay",
    "invoice total",
    "total excl",
    "total incl",
    "carried forward",
)

_INVOICE_NO_RE = re.compile(
    r"invoice\s*(?:no\.?|number|nr\.?|#)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{1,40})",
    re.IGNORECASE,
)
_DATE_LABEL_RE = re.compile(
    r"(?:invoice\s*date|issue\s*date|date)\s*[:#]?\s*"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}[./ ][A-Za-z]{3,9}[./ ][0-9]{2,4}|[0-9]{1,2}[./][0-9]{1,2}[./][0-9]{2,4})",
    re.IGNORECASE,
)
_CURRENCY = {"€": "EUR", "$": "USD", "£": "GBP"}


class OcrUnavailable(RuntimeError):
    """Raised when the PDF/OCR stack (or Tesseract binary) is not installed."""


def _num(s: str) -> Decimal:
    """Normalise a single numeric/money token to Decimal, locale-aware.

    Handles 1,234.56 (US), 1.234,56 (EU), plain 1234.56, negatives and
    parenthesised credits. The decimal separator is taken to be the *last*
    '.'/',' when both appear; a lone thousands group (",ddd"/".ddd") is treated
    as thousands.
    """
    tok = s.strip()
    neg = tok.startswith("-") or tok.startswith("(")
    tok = tok.strip("()+-").replace(" ", "")
    if not tok:
        return Decimal("0")
    if "." in tok and "," in tok:
        if tok.rfind(".") > tok.rfind(","):
            tok = tok.replace(",", "")  # comma = thousands
        else:
            tok = tok.replace(".", "").replace(",", ".")  # dot = thousands
    elif "," in tok:
        after = tok.rsplit(",", 1)[1]
        if tok.count(",") == 1 and len(after) != 3:
            tok = tok.replace(",", ".")  # comma = decimal
        else:
            tok = tok.replace(",", "")  # comma = thousands
    try:
        value = Decimal(tok)
    except InvalidOperation:
        return Decimal("0")
    return -value if neg else value


def _mask(text: str) -> str:
    """Blank out dates/times (same length) so their digits aren't read as money."""

    def blank(m: re.Match) -> str:
        return " " * (m.end() - m.start())

    return _TIME_RE.sub(blank, _DATE_RE.sub(blank, text))


def _split_leading_date(text: str) -> tuple[str, str]:
    m = _LEADING_DATE_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end() :]


def _label_of(text: str) -> str:
    """The alphabetic label of a row: text minus numbers/dates/punctuation."""
    stripped = _MONEY_RE.sub(" ", _mask(text))
    stripped = re.sub(r"[\d%.,:;#()\-/€$£]", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip().lower()


def _is_summary_row(text: str) -> bool:
    # A transaction row starts with a date — never a totals row (guards against a
    # station literally named "Total …" or "Q8" being dropped).
    if _LEADING_DATE_RE.match(text):
        return False
    label = _label_of(text)
    if not label:
        return False
    words = label.split()
    for s in _SUMMARY_LABELS:
        sw = s.split()
        # Label must START with the summary phrase and add at most 2 qualifier
        # words (e.g. "total excl vat"), so "total lyon est diesel" stays a line.
        if words[: len(sw)] == sw and (len(words) - len(sw)) <= 2:
            return True
    return False


def _to_date(value: str) -> date | None:
    value = value.strip()
    for fmt in (
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d.%m.%y",
        "%d/%m/%y",
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #
def _extract_text_layer(content: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        parts = [(page.extract_text() or "") for page in pdf.pages]
    return "\n".join(parts).strip()


def _reconstruct_lines(data: dict) -> str:
    """Rebuild visual rows from Tesseract word boxes.

    `image_to_string` reads multi-column / wide-gap tables column-by-column,
    which tears a table row (description | qty | unit | amount) into separate
    lines. We instead take the word bounding boxes and cluster them by their
    vertical position, so words sharing a row rejoin left-to-right — the row
    structure an invoice table needs.
    """
    words = []
    for i in range(len(data["text"])):
        text = (data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if not text or conf < 0:
            continue
        words.append(
            {"x": data["left"][i], "y": data["top"][i], "h": data["height"][i], "text": text}
        )

    if not words:
        return ""

    words.sort(key=lambda w: (w["y"], w["x"]))
    heights = sorted(w["h"] for w in words)
    tol = max(6.0, heights[len(heights) // 2] * 0.6)

    rows: list[list[dict]] = [[words[0]]]
    row_y = words[0]["y"]
    for w in words[1:]:
        if abs(w["y"] - row_y) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
            row_y = w["y"]

    lines = []
    for row in rows:
        row.sort(key=lambda w: w["x"])
        lines.append(" ".join(w["text"] for w in row))
    return "\n".join(lines)


def _ocr(content: bytes) -> str:
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError as e:  # pragma: no cover - env guard
        raise OcrUnavailable(str(e)) from e

    from app.services import capture_progress

    pdf = pdfium.PdfDocument(content)
    try:
        out: list[str] = []
        scale = _OCR_DPI / 72.0
        # WO-X: this loop is the only genuinely slow phase of a capture, and the
        # only one that is divisible into countable units — so it is the only one
        # that reports a page count. The total is what will ACTUALLY be read (the
        # DoS cap applied), not the document's page count, so "page 20 of 20" on a
        # 60-page file is not a lie about work still to come.
        total = min(len(pdf), _MAX_OCR_PAGES)
        capture_progress.report(capture_progress.OCR, pages_done=0, pages_total=total)
        for i in range(total):  # cap pages (DoS guard)
            page = pdf[i]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            out.append(_reconstruct_lines(data))
            capture_progress.report(capture_progress.OCR, pages_done=i + 1, pages_total=total)
        return "\n".join(out).strip()
    finally:
        pdf.close()


def extract_text(content: bytes) -> tuple[str, str]:
    """Return (text, method) where method is 'text-layer' or 'ocr'."""
    from app.services import capture_progress

    capture_progress.report(capture_progress.READING)
    try:
        text = _extract_text_layer(content)
    except ImportError as e:  # pragma: no cover - env guard
        raise OcrUnavailable(str(e)) from e

    if len(text) >= _TEXT_LAYER_MIN_CHARS:
        return text, "text-layer"

    # Scanned / image-only PDF → OCR.
    return _ocr(content), "ocr"


# --------------------------------------------------------------------------- #
# Heuristic invoice parsing
# --------------------------------------------------------------------------- #
def _find_labeled_amount(
    lines: list[str], keywords: tuple[str, ...], exclude: tuple[str, ...] = ()
) -> Decimal | None:
    # Scan bottom-up (totals sit at the foot) and ignore transaction rows so a
    # station literally named "Total …" can't be mistaken for the grand total.
    for line in reversed(lines):
        if _LEADING_DATE_RE.match(line):
            continue
        low = line.lower()
        if any(k in low for k in keywords) and not any(x in low for x in exclude):
            amts = _MONEY_RE.findall(_mask(line))
            if amts:
                return _num(amts[-1])
    return None


def _parse_row(line: str) -> LineItemIn | None:
    """Parse ONE reconstructed row into a line item, or None if it isn't one.

    Transaction-aware: a leading date is kept as part of the description (so a
    fuel/toll statement's date+station+product survives), dates/times inside the
    row are masked before money parsing, and the rightmost money token is the
    line amount. Any number left trailing the description is the quantity.
    """
    if _is_summary_row(line):
        return None

    date_prefix, rest = _split_leading_date(line)
    masked = _mask(rest)
    money = list(_MONEY_RE.finditer(masked))
    if not money:
        return None

    first = money[0].start()
    desc_core = rest[:first].strip(" .\t-|:#")
    amounts = [_num(m.group()) for m in money]
    amount = amounts[-1]
    unit = amounts[-2] if len(amounts) >= 2 else amount

    qty = Decimal("1")
    qm = re.search(r"^(.*?)(\d+(?:[.,]\d+)?)\s*$", desc_core)
    if len(amounts) >= 2 and qm and qm.group(1).strip():
        desc_core = qm.group(1).strip()
        qty = _num(qm.group(2))

    description = (f"{date_prefix} {desc_core}".strip() if date_prefix else desc_core).strip()
    if not description or description.replace(" ", "").isdigit():
        description = desc_core or date_prefix or "Item"

    if amount <= 0 and qty and unit:
        amount = qty * unit
    if amount <= 0:
        return None

    return LineItemIn(
        description=description[:500],
        category="uncategorized",
        quantity=qty if qty > 0 else Decimal("1"),
        unit_price=unit if unit > 0 else amount,
        amount=amount,
        tax_rate=Decimal("0"),
    )


def _extract_line_items(lines: list[str]) -> list[LineItemIn]:
    items: list[LineItemIn] = []
    for line in lines:
        item = _parse_row(line)
        if item is not None:
            items.append(item)
    return items


def parse_invoice_text(text: str, filename: str, method: str) -> ParsedInvoiceDraft:
    warnings: list[str] = [
        f"Extracted via {method.upper()} — verify every figure before saving."
        if method == "ocr"
        else "Parsed from the PDF text layer — verify before saving."
    ]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Vendor: first line that looks like a name.
    vendor = next(
        (
            ln
            for ln in lines
            if len(ln) > 2 and any(c.isalpha() for c in ln) and "invoice" not in ln.lower()
        ),
        None,
    )

    m = _INVOICE_NO_RE.search(text)
    invoice_number = m.group(1) if m else _stem(filename)
    if not m:
        warnings.append("Invoice number not detected; used the filename.")

    issue = None
    dm = _DATE_LABEL_RE.search(text)
    if dm:
        issue = _to_date(dm.group(1))
    if issue is None:
        issue = date.today()
        warnings.append("Issue date not detected; defaulted to today.")

    currency = "EUR"
    for sym, code in _CURRENCY.items():
        if sym in text:
            currency = code
            break

    items = _extract_line_items(lines)

    subtotal = _find_labeled_amount(lines, ("subtotal", "sub-total", "sub total"))
    tax = _find_labeled_amount(lines, ("vat", "tax"))
    total = _find_labeled_amount(
        lines,
        ("total", "amount due", "balance due"),
        exclude=("subtotal", "sub-total", "sub total"),
    )

    # If we found a subtotal + tax, infer one VAT rate and apply it to the lines
    # so the saved invoice's recomputed tax matches the document.
    if items and subtotal and tax and subtotal > 0:
        rate = (tax / subtotal * Decimal("100")).quantize(Decimal("0.01"))
        if rate <= 100:
            items = [li.model_copy(update={"tax_rate": rate}) for li in items]
            warnings.append(
                f"Applied inferred VAT rate {rate}% to all lines (subtotal {subtotal}, tax {tax})."
            )

    if not items:
        if total:
            items = [
                LineItemIn(
                    description="Invoice total (OCR-detected)",
                    category="uncategorized",
                    quantity=Decimal("1"),
                    unit_price=total,
                    amount=total,
                    tax_rate=Decimal("0"),
                )
            ]
            warnings.append("No line items detected; captured the document total as a single line.")
        else:
            warnings.append("No line items or total detected — enter them manually.")

    if total is not None:
        warnings.append(
            f"Document total on page: {currency} {total} — reconcile against the parsed lines."
        )

    draft = InvoiceCreate(
        vendor_name=vendor,
        invoice_number=invoice_number,
        issue_date=issue,
        currency=currency,
        source_filename=filename,
        line_items=items,
    )
    return ParsedInvoiceDraft(draft=draft, warnings=warnings, method=method)


def _stem(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0][:120] or "INV"


def parse_pdf(filename: str, content: bytes) -> ParsedInvoiceDraft:
    """Parse a PDF into a draft invoice.

    Deterministic-first: if the PDF is a Factur-X/ZUGFeRD hybrid carrying an
    embedded e-invoice XML, read that exactly (no OCR). Otherwise use the text
    layer, falling back to OCR for scanned documents.
    """
    from app.services import einvoice

    try:
        embedded = einvoice.extract_embedded_xml(content)
    except Exception:  # never let attachment probing break the PDF path
        embedded = None
    if embedded:
        result = einvoice.parse_xml_bytes(embedded, filename)
        result.warnings.insert(
            0, "Read the e-invoice XML embedded in this PDF (Factur-X/ZUGFeRD) — no OCR needed."
        )
        return result

    text, method = extract_text(content)
    if not text:
        raise ValueError("Could not read any text from the PDF (empty or unreadable).")
    from app.services import capture_progress

    capture_progress.report(capture_progress.INTERPRETING)
    return parse_invoice_text(text, filename, method)
