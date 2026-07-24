"""PDF ingestion tests: text-layer extraction and the Tesseract OCR fallback.

These require the optional PDF/OCR stack; they skip cleanly if it (or the
tesseract binary) is not installed, so the core suite stays green everywhere.
"""

import io
import shutil
from decimal import Decimal

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("pdfplumber")
pytest.importorskip("pypdfium2")
pytest.importorskip("pytesseract")
pytest.importorskip("PIL")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

_LINES = [
    "Acme Cloud Services Ltd",
    "123 Server Road, Dublin, IE",
    "",
    "INVOICE",
    "Invoice Number: INV-2026-0042",
    "Invoice Date: 2026-03-15",
    "",
    "Description                        Qty     Unit Price      Amount",
    f"{'Cloud compute':<34} {'2':>3}     {'100.00':>9}     {'200.00':>9}",
    f"{'Object storage':<34} {'1':>3}     {'50.00':>9}     {'50.00':>9}",
    "",
    "Subtotal                                                   250.00",
    "VAT (21%)                                                   52.50",
    "Total                                                      302.50",
]


def _text_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Courier", 11)
    y = 800
    for ln in _LINES:
        c.drawString(40, y, ln)
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


def _scanned_pdf() -> bytes:
    """Image-only PDF (no text layer) → forces the OCR path."""
    W, H = 1240, 1754
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 26)
    y = 80
    for ln in _LINES:
        d.text((70, y), ln, fill="black", font=font)
        y += 46
    png = io.BytesIO()
    img.save(png, format="PNG")
    png.seek(0)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.drawImage(ImageReader(png), 0, 0, width=W, height=H)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_pdf_text_layer_upload_and_save(auth_client, parse_upload):
    files = {"file": ("invoice.pdf", io.BytesIO(_text_pdf()), "application/pdf")}
    up = await parse_upload(auth_client, files)
    draft = up["draft"]
    assert draft["invoice_number"] == "INV-2026-0042"
    assert draft["issue_date"] == "2026-03-15"
    assert len(draft["line_items"]) == 2

    saved = await auth_client.post("/api/v1/invoices", json=draft)
    assert saved.status_code == 201, saved.text
    body = saved.json()
    # 2*100 + 1*50 = 250 subtotal; inferred 21% VAT ⇒ 52.50 tax ⇒ 302.50 total
    assert body["subtotal"] == "250.00"
    assert body["tax_amount"] == "52.50"
    assert body["total"] == "302.50"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not installed")
def test_pdf_ocr_fallback_reads_scanned_invoice():
    from app.services import pdf_ocr

    text, method = pdf_ocr.extract_text(_scanned_pdf())
    assert method == "ocr"
    assert "INV-2026-0042" in text.replace(" ", "").replace("O", "0") or "INV-2026-0042" in text

    result = pdf_ocr.parse_pdf("scan.pdf", _scanned_pdf())
    d = result.draft
    assert d.invoice_number == "INV-2026-0042"
    assert len(d.line_items) == 2
    assert sum((li.amount for li in d.line_items), start=Decimal("0")) == Decimal("250.00")
