"""Modular activation + EU-compliant invoice issuing (EN 16931) with a PDF that
carries embedded Factur-X XML."""
from decimal import Decimal

import pytest

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}

INVOICE = {
    "buyer_name": "Globex SARL",
    "buyer_vat_number": "FR40303265045",
    "buyer_address_line1": "10 Rue de Rivoli",
    "buyer_city": "Paris",
    "buyer_postal_code": "75001",
    "buyer_country": "FR",
    "issue_date": "2026-05-10",
    "lines": [
        {"description": "Consulting", "quantity": "2", "unit_price": "100.00", "vat_rate": "19"},
        {"description": "Support", "quantity": "1", "unit_price": "50.00", "vat_rate": "7"},
    ],
}


async def _activate(auth_client):
    r = await auth_client.put("/api/v1/issuer", json=ISSUER)
    assert r.status_code == 200, r.text
    assert r.json()["is_complete"] is True
    m = await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    assert m.status_code == 200
    assert m.json()["enabled"] is True and m.json()["ready"] is True


@pytest.mark.asyncio
async def test_modules_list_and_core_protected(auth_client):
    mods = (await auth_client.get("/api/v1/modules")).json()
    by = {m["key"]: m for m in mods}
    assert by["issuing"]["enabled"] is False and by["issuing"]["core"] is False
    assert by["analytics"]["core"] is True and by["analytics"]["enabled"] is True
    # core modules can't be toggled
    r = await auth_client.put("/api/v1/modules/analytics", json={"enabled": False})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_issuing_gated_by_module_and_issuer(auth_client):
    # module off → 403
    r = await auth_client.post("/api/v1/issued", json=INVOICE)
    assert r.status_code == 403

    # module on but issuer incomplete → 409
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    r = await auth_client.post("/api/v1/issued", json=INVOICE)
    assert r.status_code == 409
    assert "registration details" in r.json()["detail"]


@pytest.mark.asyncio
async def test_issue_computes_vat_and_sequential_numbers(auth_client):
    await _activate(auth_client)

    r = await auth_client.post("/api/v1/issued", json=INVOICE)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["number"] == "INV-2026-0001"
    # 200 + 50 = 250 base; 19% of 200 = 38.00, 7% of 50 = 3.50 → 41.50 tax
    assert inv["subtotal"] == "250.00"
    assert inv["tax_total"] == "41.50"
    assert inv["total"] == "291.50"
    rates = {b["rate"]: b for b in inv["vat_breakdown"]}
    assert rates["19.00"]["vat"] == "38.00"
    assert rates["7.00"]["vat"] == "3.50"
    assert inv["due_date"]  # defaulted from payment terms

    # sequential
    r2 = await auth_client.post("/api/v1/issued", json=INVOICE)
    assert r2.json()["number"] == "INV-2026-0002"


@pytest.mark.asyncio
async def test_reverse_charge_zero_vat_with_note(auth_client):
    await _activate(auth_client)
    payload = {**INVOICE, "vat_scheme": "reverse_charge"}
    inv = (await auth_client.post("/api/v1/issued", json=payload)).json()
    assert inv["tax_total"] == "0.00"
    assert inv["total"] == "250.00"
    assert "Reverse charge" in inv["note"]


@pytest.mark.asyncio
async def test_pdf_has_embedded_xml_that_roundtrips(auth_client):
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    from app.services import einvoice

    await _activate(auth_client)
    inv = (await auth_client.post("/api/v1/issued", json=INVOICE)).json()

    pdf = await auth_client.get(f"/api/v1/issued/{inv['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    body = pdf.content
    assert body[:5] == b"%PDF-"

    # Our own reader must find + parse the embedded Factur-X XML.
    xml = einvoice.extract_embedded_xml(body)
    assert xml is not None
    draft = einvoice.parse_xml_bytes(xml, "roundtrip.xml").draft
    assert draft.invoice_number == "INV-2026-0001"
    assert draft.vendor_name == "InvoiceIQ Demo BV"
    assert sum((li.amount for li in draft.line_items), start=Decimal("0")) == Decimal("250.00")


@pytest.mark.asyncio
async def test_standalone_xml_endpoint(auth_client):
    await _activate(auth_client)
    inv = (await auth_client.post("/api/v1/issued", json=INVOICE)).json()
    x = await auth_client.get(f"/api/v1/issued/{inv['id']}/xml")
    assert x.status_code == 200
    assert b"CrossIndustryInvoice" in x.content
    assert b"NL123456789B01" in x.content  # seller VAT present


# Minimal valid 1x1 PNG (signature + IHDR + IEND).
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_logo_upload_is_scanned(auth_client):
    from app.services import filesec

    # A valid PNG is accepted.
    ok = await auth_client.post("/api/v1/issuer/logo", files={"file": ("logo.png", _PNG, "image/png")})
    assert ok.status_code == 200, ok.text
    assert ok.json()["has_logo"] is True

    # A PNG carrying the EICAR signature is blocked by the security gate.
    infected = _PNG + filesec.EICAR
    bad = await auth_client.post("/api/v1/issuer/logo", files={"file": ("logo.png", infected, "image/png")})
    assert bad.status_code == 415

    # A PDF disguised as a logo is refused (logos are image-only).
    pdf = await auth_client.post("/api/v1/issuer/logo", files={"file": ("logo.png", b"%PDF-1.4 x", "application/pdf")})
    assert pdf.status_code == 415
