"""Pluggable extraction providers — the seam so extraction can later use different
OCR, document-AI, or e-invoice parsers without touching the upload route or worker.

A provider turns raw upload bytes into a `ProviderResult`: a draft invoice plus
per-field `FieldCapture`s carrying confidence + provenance. Providers are tried in
registration order (deterministic-first); the first whose `handles()` returns True
wins. The existing parsers (e-invoice XML, PDF text/OCR, CSV, JSON) are wrapped as
the built-in providers; a future OCR/doc-AI vendor is one more `ExtractionProvider`
subclass + `register()` call.

Honest confidence (the request: "do not pretend extraction is always accurate"):
  - structured/deterministic (e-invoice XML, CSV, JSON): exact → confidence None
    (there is no probabilistic score; None means "exact", not "unknown").
  - PDF text-layer: 0.85 (digital text, but field roles are inferred).
  - PDF OCR (scanned image): 0.55 (genuinely uncertain).
A field below `LOW_CONFIDENCE_THRESHOLD`, or with a non-"extracted" status, is
flagged `low_confidence` so the human-review queue surfaces it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from dataclasses import field as dc_field
from decimal import Decimal

from app.schemas.invoice import InvoiceCreate, ParsedInvoiceDraft

LOW_CONFIDENCE_THRESHOLD = Decimal("0.75")

# Per-method confidence for a well-read field. None = deterministic/exact.
_CONFIDENCE: dict[str, Decimal | None] = {
    "e-invoice-xml": None,
    "csv": None,
    "json": None,
    "text-layer": Decimal("0.85"),
    "ocr": Decimal("0.55"),
}

# The five top-level header fields we record provenance for (kept stable — the
# extraction-fields test asserts exactly these five for the structured parsers).
_HEADER_FIELDS = ("invoice_number", "vendor_name", "issue_date", "due_date", "currency")


@dataclass
class FieldCapture:
    """One captured field with honest provenance."""

    field: str
    status: str  # extracted | defaulted | missing
    original: str | None = None
    normalized: str | None = None
    confidence: Decimal | None = None
    provider: str = ""
    low_confidence: bool = False

    @property
    def value(self) -> str | None:
        return self.normalized if self.normalized is not None else self.original


@dataclass
class ProviderResult:
    """What a provider returns: the draft plus per-field captures + metadata."""

    method: str
    draft: InvoiceCreate
    provider: str
    warnings: list[str] = dc_field(default_factory=list)
    fields: list[FieldCapture] = dc_field(default_factory=list)


class ExtractionProvider(ABC):
    """The interface every extraction backend implements."""

    name: str = "provider"

    @abstractmethod
    def handles(self, filename: str, content: bytes) -> bool:
        """Whether this provider should parse the given file."""

    @abstractmethod
    def extract(self, filename: str, content: bytes) -> ProviderResult:
        """Parse the bytes into a draft + field captures. Raises ValueError on bad
        input (the caller turns that into a failed extraction run)."""


def _flag(status: str, confidence: Decimal | None) -> bool:
    """A field needs a human look if it wasn't cleanly extracted, or its score is
    below the threshold."""
    if status != "extracted":
        return True
    return confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD


def _header_captures(draft: InvoiceCreate, *, method: str, provider: str) -> list[FieldCapture]:
    """Build provenance for the five header fields from a draft, for providers
    (OCR/e-invoice) whose underlying parser doesn't emit per-field provenance
    itself. Status is inferred from presence; confidence is per-method."""
    conf = _CONFIDENCE.get(method)
    values = {
        "invoice_number": draft.invoice_number,
        "vendor_name": draft.vendor_name,
        "issue_date": draft.issue_date.isoformat() if draft.issue_date else None,
        "due_date": draft.due_date.isoformat() if draft.due_date else None,
        "currency": draft.currency,
    }
    out: list[FieldCapture] = []
    for name in _HEADER_FIELDS:
        v = values[name]
        status = "extracted" if v not in (None, "") else "missing"
        out.append(
            FieldCapture(
                field=name,
                status=status,
                original=v,
                normalized=v,
                confidence=conf if status == "extracted" else None,
                provider=provider,
                low_confidence=_flag(status, conf),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Built-in providers — thin wrappers over the existing deterministic parsers.
# --------------------------------------------------------------------------- #


class PdfProvider(ExtractionProvider):
    name = "pdf"

    def handles(self, filename: str, content: bytes) -> bool:
        return filename.lower().endswith(".pdf")

    def extract(self, filename: str, content: bytes) -> ProviderResult:
        from app.services import pdf_ocr

        try:
            parsed = pdf_ocr.parse_pdf(filename, content)
        except pdf_ocr.OcrUnavailable as exc:
            raise ValueError(
                "PDF support is not installed on the server "
                f"(pdfplumber/pypdfium2/pytesseract + tesseract binary): {exc}"
            )
        # pdf_ocr reports the method (embedded XML / text-layer / OCR); label the
        # provider accordingly so the metadata reflects the real backend used.
        prov = {"e-invoice-xml": "pdf-einvoice", "text-layer": "pdf-text", "ocr": "pdf-ocr"}.get(
            parsed.method, "pdf"
        )
        return ProviderResult(
            method=parsed.method,
            draft=parsed.draft,
            provider=prov,
            warnings=list(parsed.warnings),
            fields=_header_captures(parsed.draft, method=parsed.method, provider=prov),
        )


class EInvoiceProvider(ExtractionProvider):
    name = "einvoice"

    def handles(self, filename: str, content: bytes) -> bool:
        from app.services import einvoice

        lower = filename.lower()
        if lower.endswith(".pdf"):
            return False  # the PDF provider handles embedded XML
        return lower.endswith(".xml") or (
            not lower.endswith((".csv", ".json")) and einvoice.looks_like_einvoice(content)
        )

    def extract(self, filename: str, content: bytes) -> ProviderResult:
        from app.services import einvoice

        parsed = einvoice.parse_xml_bytes(content, filename)  # method "e-invoice-xml"
        return ProviderResult(
            method=parsed.method,
            draft=parsed.draft,
            provider=self.name,
            warnings=list(parsed.warnings),
            fields=_header_captures(parsed.draft, method=parsed.method, provider=self.name),
        )


def _captures_from_provenance(fields, provider: str) -> list[FieldCapture]:
    """Adapt the CSV/JSON parser's FieldProvenance (5 header fields, no confidence)
    to FieldCapture. Deterministic → confidence stays None; a defaulted/missing
    field is still flagged so the review queue surfaces it."""
    return [
        FieldCapture(
            field=p.field,
            status=p.status,
            original=p.value,
            normalized=p.value,
            confidence=None,
            provider=provider,
            low_confidence=(p.status != "extracted"),
        )
        for p in fields
    ]


class JsonProvider(ExtractionProvider):
    name = "json"

    def handles(self, filename: str, content: bytes) -> bool:
        return filename.lower().endswith(".json")

    def extract(self, filename: str, content: bytes) -> ProviderResult:
        from app.services import parser

        warnings: list[str] = []
        draft, prov = parser._parse_json(content, filename, warnings)
        if not draft.line_items:
            warnings.append("No line items were found in the file")
        return ProviderResult(
            method="json",
            draft=draft,
            provider=self.name,
            warnings=warnings,
            fields=_captures_from_provenance(prov, self.name),
        )


class CsvProvider(ExtractionProvider):
    name = "csv"

    def handles(self, filename: str, content: bytes) -> bool:
        return filename.lower().endswith(".csv")

    def extract(self, filename: str, content: bytes) -> ProviderResult:
        from app.services import parser

        warnings: list[str] = []
        draft, prov = parser._parse_csv(content, filename, warnings)
        if not draft.line_items:
            warnings.append("No line items were found in the file")
        return ProviderResult(
            method="csv",
            draft=draft,
            provider=self.name,
            warnings=warnings,
            fields=_captures_from_provenance(prov, self.name),
        )


class ImageProvider(ExtractionProvider):
    """OCR for scanned-image invoices (JPEG / PNG). Reuses the same text→draft
    heuristics as the PDF-OCR path. Degrades gracefully to a ValueError (→ a failed
    extraction run with a clear reason) when the OCR stack isn't installed, so an
    image upload is always *accepted* even where OCR can't run."""

    name = "image-ocr"

    def handles(self, filename: str, content: bytes) -> bool:
        return filename.lower().endswith((".png", ".jpg", ".jpeg"))

    def extract(self, filename: str, content: bytes) -> ProviderResult:
        import io

        try:
            import pytesseract
            from PIL import Image
        except Exception as exc:  # noqa: BLE001 - report as a clean parse failure
            raise ValueError(f"Image OCR is not installed on the server: {exc}")
        try:
            text = pytesseract.image_to_string(Image.open(io.BytesIO(content)))
        except Exception as exc:  # noqa: BLE001 - unreadable image
            raise ValueError(f"Could not OCR the image: {exc}")

        from app.services import pdf_ocr

        parsed = pdf_ocr.parse_invoice_text(text, filename, "ocr")
        return ProviderResult(
            method="ocr",
            draft=parsed.draft,
            provider=self.name,
            warnings=list(parsed.warnings),
            fields=_header_captures(parsed.draft, method="ocr", provider=self.name),
        )


# Registration order = try order (deterministic-first): PDF, e-invoice XML, JSON,
# CSV, then image OCR.
_PROVIDERS: list[ExtractionProvider] = [
    PdfProvider(),
    EInvoiceProvider(),
    JsonProvider(),
    CsvProvider(),
    ImageProvider(),
]


def register(provider: ExtractionProvider, *, first: bool = False) -> None:
    """Add a provider. `first=True` gives it priority (e.g. a document-AI backend
    that should be tried ahead of the built-ins)."""
    if first:
        _PROVIDERS.insert(0, provider)
    else:
        _PROVIDERS.append(provider)


def providers() -> list[ExtractionProvider]:
    return list(_PROVIDERS)


def select(filename: str, content: bytes) -> ExtractionProvider:
    for p in _PROVIDERS:
        try:
            if p.handles(filename, content):
                return p
        except Exception:  # noqa: BLE001 - a provider's sniff must never break selection
            continue
    raise ValueError("Unsupported file type. Upload a .pdf, .xml, .csv, or .json file.")


def run(filename: str, content: bytes) -> ProviderResult:
    """Select a provider and extract. Raises ValueError for unsupported/bad input."""
    return select(filename, content).extract(filename, content)


def to_draft(result: ProviderResult) -> ParsedInvoiceDraft:
    """Map a ProviderResult onto the ParsedInvoiceDraft the route/worker expect."""
    from app.schemas.invoice import FieldProvenance

    fields = [
        FieldProvenance(
            field=c.field,
            value=c.value,
            status=c.status,
            confidence=c.confidence,
            original_value=c.original,
            normalized_value=c.normalized,
            reviewed_value=None,
            provider=c.provider or result.provider,
            low_confidence=c.low_confidence,
        )
        for c in result.fields
    ]
    return ParsedInvoiceDraft(
        draft=result.draft, warnings=result.warnings, method=result.method, fields=fields
    )
