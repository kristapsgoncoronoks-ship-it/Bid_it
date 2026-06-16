"""OCR / scanned-PDF seam (Tier C4): a pluggable, default-graceful on-prem OCR fallback for
image-only PDFs that carry no embedded text. When no OCR backend is available the behaviour
is byte-identical to before; when OCR recovers text the draft is flagged for careful review.
Tests are offline — the OCR backend is stubbed, never a real tesseract call."""
import io

import extract as EX


def _blank_pdf():
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_ocr_text_none_backend_returns_empty():
    assert EX.ocr_text(b"%PDF-1.4", backend="none") == ""


def test_ocr_text_unavailable_is_graceful(monkeypatch):
    # auto + no tesseract -> '' (never raises), behaviour unchanged from before OCR
    monkeypatch.setattr(EX, "_tesseract_ready", lambda: False)
    assert EX.ocr_text(_blank_pdf(), backend="auto") == ""


def test_ocr_backend_failure_degrades(monkeypatch):
    monkeypatch.setitem(EX._OCR, "tesseract", lambda b: (_ for _ in ()).throw(RuntimeError("boom")))
    assert EX.ocr_text(_blank_pdf(), backend="tesseract") == ""


def test_looks_scanned_threshold():
    assert EX._looks_scanned("")
    assert EX._looks_scanned("   \n  ")
    assert not EX._looks_scanned("BE-001 Germany net 100.00 vat 19.00 EUR total")


def test_pdf_text_or_ocr_uses_ocr_when_scanned(monkeypatch):
    # a blank PDF has ~no embedded text -> OCR is consulted; stub returns recovered text
    monkeypatch.setattr(EX, "ocr_text", lambda b, backend=None: "INV-1 Germany 100.00 19.00")
    text, used = EX.pdf_text_or_ocr(_blank_pdf())
    assert used is True and "INV-1" in text


def test_pdf_text_or_ocr_skips_ocr_when_text_present(monkeypatch):
    monkeypatch.setattr(EX, "pdf_text", lambda b: "a real text layer with plenty of content here")
    called = {"n": 0}
    monkeypatch.setattr(EX, "ocr_text", lambda b, backend=None: called.__setitem__("n", called["n"] + 1) or "x")
    text, used = EX.pdf_text_or_ocr(b"whatever")
    assert used is False and called["n"] == 0


def test_extract_plain_pdf_ocr_flagged(monkeypatch):
    # blank (scanned-looking) PDF, OCR stubbed to return a Eurowag-shaped marker so the
    # deterministic parser path is exercised end-to-end and the draft is OCR-flagged.
    monkeypatch.setattr(EX, "ocr_text",
                        lambda b, backend=None: "W.A.G. payment Total payment 123456 EUR")
    d = EX.extract(_blank_pdf(), "scan.pdf", backend="parser")
    assert d.get("ocr") is True
    assert "OCR" in d.get("notes", "")
    assert d["confidence"] != "high"


def test_extract_plain_no_ocr_unchanged(monkeypatch):
    # no OCR available -> empty text -> empty/manual draft, no 'ocr' flag (unchanged path)
    monkeypatch.setattr(EX, "ocr_text", lambda b, backend=None: "")
    d = EX.extract(_blank_pdf(), "scan.pdf", backend="none")
    assert "ocr" not in d
