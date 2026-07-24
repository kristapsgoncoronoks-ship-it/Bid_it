"""OCR/extraction must read EVERY line of a multi-row transaction statement
(e.g. a fuel/toll card statement), not just simple invoice lines."""

import io
from decimal import Decimal

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("pdfplumber")
pytest.importorskip("pypdfium2")
pytest.importorskip("pytesseract")
pytest.importorskip("PIL")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.services import pdf_ocr  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# 12 transactions. Two stations are literally named "Total …" — a regression
# guard: those rows must NOT be dropped as summary/total rows.
TXNS = [
    ("01.06.2026", "Shell Berlin Mitte", "Diesel", "52.30", "1.62", "84.73"),
    ("02.06.2026", "Aral Munich Sud", "Diesel", "48.10", "1.60", "76.96"),
    ("04.06.2026", "Total Lyon Est", "Diesel", "61.00", "1.71", "104.31"),
    ("06.06.2026", "BP Rotterdam", "AdBlue", "10.00", "0.95", "9.50"),
    ("07.06.2026", "Esso Antwerp", "Diesel", "55.40", "1.66", "91.96"),
    ("09.06.2026", "OMV Vienna", "Diesel", "50.00", "1.59", "79.50"),
    ("11.06.2026", "Q8 Milan Ovest", "Toll", "1.00", "24.80", "24.80"),
    ("13.06.2026", "Shell Hamburg", "Diesel", "58.20", "1.63", "94.87"),
    ("15.06.2026", "Aral Cologne", "Diesel", "47.80", "1.61", "76.96"),
    ("17.06.2026", "Total Paris Nord", "Diesel", "62.50", "1.73", "108.13"),
    ("19.06.2026", "BP Brussels", "Shop", "1.00", "12.40", "12.40"),
    ("21.06.2026", "Esso Frankfurt", "Diesel", "53.90", "1.64", "88.40"),
]
EXPECTED_TOTAL = sum(Decimal(t[5]) for t in TXNS)  # 852.52

HEADER = [
    "EuroFuel Card Services GmbH",
    "Invoice Number: EF-2026-000917",
    "Invoice Date: 2026-06-30",
    "",
    "Date         Station                 Product   Litres   Unit     Amount",
]


def _lines():
    out = list(HEADER)
    for d, station, prod, lit, unit, amt in TXNS:
        out.append(f"{d}  {station:<22} {prod:<8} {lit:>7} {unit:>7} {amt:>9}")
    out += ["", f"Subtotal {EXPECTED_TOTAL:>52}", "VAT (0%) 0.00", f"Total {EXPECTED_TOTAL:>55}"]
    return out


def _text_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(720, 900))
    c.setFont("Courier", 9)
    y = 860
    for ln in _lines():
        c.drawString(20, y, ln)
        y -= 16
    c.showPage()
    c.save()
    return buf.getvalue()


def _scanned_pdf() -> bytes:
    W, H = 1400, 2000
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 24)
    y = 60
    for ln in _lines():
        draw.text((60, y), ln, fill="black", font=font)
        y += 40
    png = io.BytesIO()
    img.save(png, format="PNG")
    png.seek(0)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.drawImage(ImageReader(png), 0, 0, width=W, height=H)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_text_layer_reads_all_transactions_exactly():
    result = pdf_ocr.parse_pdf("fuel.pdf", _text_pdf())
    items = result.draft.line_items
    # Every transaction row captured — no more, no less.
    assert len(items) == len(TXNS)
    # Exact amounts from the text layer.
    assert sum((li.amount for li in items), start=Decimal("0")) == EXPECTED_TOTAL
    descs = " | ".join(li.description for li in items)
    # The "Total …" station rows survived (not dropped as summary rows).
    assert "Lyon" in descs and "Paris" in descs
    # Dates are preserved on the transaction descriptions.
    assert any(li.description.startswith("01.06.2026") for li in items)


def test_scanned_ocr_reads_all_transactions():
    result = pdf_ocr.parse_pdf("fuel_scan.pdf", _scanned_pdf())
    items = result.draft.line_items
    # Completeness is the requirement: OCR may misread a digit, but every
    # transaction row must still be read as a line.
    assert len(items) == len(TXNS)
    assert all((li.amount or 0) > 0 for li in items)
    descs = " | ".join(li.description for li in items)
    assert "Lyon" in descs and "Paris" in descs
