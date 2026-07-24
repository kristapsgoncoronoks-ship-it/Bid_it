"""Advisory receipt OCR auto-fill: field-extraction heuristics + the /receipt-scan
endpoint (text-layer PDF path, and the security gate). OCR is advisory — it only
suggests, never writes."""

import io
from decimal import Decimal

import pytest

from app.services import receipt_ocr

_SAMPLE = "CAFE CENTRAL\n2026-05-01\nCoffee 3.50\nCroissant 4.15\nSubtotal 35.00\nVAT 7.35\nTotal EUR 42.35"


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _activate(auth_client):
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    ).status_code == 200


def _receipt_pdf(lines: list[str]) -> bytes:
    """A text-layer PDF receipt (no OCR needed to read it back)."""
    from reportlab.lib.pagesizes import A6
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A6)
    y = 260
    for ln in lines:
        c.drawString(20, y, ln)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Heuristics (deterministic, no OCR)
# --------------------------------------------------------------------------- #
def test_parse_picks_total_not_subtotal():
    s = receipt_ocr.parse_receipt_text(_SAMPLE)
    assert s.amount == Decimal("42.35")  # not the 35.00 subtotal
    assert s.vat_amount == Decimal("7.35")
    assert s.merchant == "CAFE CENTRAL"
    assert s.spend_date is not None and s.spend_date.isoformat() == "2026-05-01"
    assert s.currency == "EUR"


def test_parse_falls_back_to_largest_money_without_total_label():
    s = receipt_ocr.parse_receipt_text("SHOP\n2026-03-02\nItem A 5.00\nItem B 12.00")
    assert s.amount == Decimal("12.00")


def test_parse_detects_currency_symbol():
    s = receipt_ocr.parse_receipt_text("STORE\nTotal $18.00")
    assert s.currency == "USD"


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_receipt_scan_endpoint_reads_pdf(auth_client):
    await _activate(auth_client)
    pdf = _receipt_pdf(
        ["CAFE CENTRAL", "2026-05-01", "Subtotal 35.00", "VAT 7.35", "Total EUR 42.35"]
    )
    r = await auth_client.post(
        "/api/v1/expenses/receipt-scan",
        files={"file": ("receipt.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["amount"]) == Decimal("42.35")
    assert body["method"] == "text-layer"


@pytest.mark.asyncio
async def test_receipt_scan_rejects_executable(auth_client):
    await _activate(auth_client)
    r = await auth_client.post(
        "/api/v1/expenses/receipt-scan",
        files={"file": ("x.exe", io.BytesIO(b"MZ\x90\x00evil"), "application/octet-stream")},
    )
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_receipt_scan_rejects_unsupported_type(auth_client):
    await _activate(auth_client)
    r = await auth_client.post(
        "/api/v1/expenses/receipt-scan",
        files={"file": ("notes.csv", io.BytesIO(b"a,b,c\n1,2,3\n"), "text/csv")},
    )
    assert r.status_code == 415  # RECEIPT_KINDS = pdf/png/jpeg only
