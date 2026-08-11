"""The EN-16931 / Factur-X line is a CLAIM ABOUT THE DOCUMENT, so it may only
appear when it is true.

`invoice_pdf.build_pdf` is a public entry point whose `xml_bytes` can be empty.
Before this test it printed "Factur-X XML embedded" unconditionally AND attached
an empty `factur-x.xml`, so a PDF carrying no structured data asserted that it
did — a false statement on a financial document, and one whose falseness a
reader cannot see. Production (`issued._render_pdf`) always supplies real CII,
so the case was latent; these tests keep it that way rather than trusting that
no future caller passes an empty payload.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from xml.etree import ElementTree

from pypdf import PdfReader

from app.services import invoice_pdf, vat

_CLAIM = "Factur-X XML embedded"
_XML = b"<?xml version='1.0'?><CrossIndustryInvoice/>"


def _invoice() -> SimpleNamespace:
    return SimpleNamespace(
        number="INV-TEST-0001",
        doc_type="invoice",
        currency="EUR",
        issue_date=date(2026, 1, 31),
        due_date=date(2026, 2, 14),
        vat_scheme="standard",
        po_reference=None,
        tax_exemption_reason=None,
        buyer_name="Test Buyer OU",
        buyer_address_line1="1 Test Street",
        buyer_postal_code="00000",
        buyer_city="Testville",
        buyer_country="Estonia",
        buyer_vat_number=None,
    )


def _seller() -> dict[str, str]:
    return {"legal_name": "Test Seller OU", "address_line1": "2 Test Road", "city": "Testville"}


def _vat_result() -> vat.VatResult:
    return vat.compute(
        [
            {
                "description": "Service",
                "quantity": Decimal("1"),
                "unit_price": Decimal("100.00"),
                "vat_rate": Decimal("22"),
            }
        ]
    )


def _render(xml_bytes: bytes) -> bytes:
    return invoice_pdf.build_pdf(_invoice(), _seller(), _vat_result(), xml_bytes, None)


def _text(pdf: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


def _attachment_names(pdf: bytes) -> list[str]:
    return list(getattr(PdfReader(io.BytesIO(pdf)), "attachments", {}) or {})


def test_the_compliance_claim_is_printed_when_the_xml_is_really_embedded():
    pdf = _render(_XML)
    assert _CLAIM in _text(pdf)
    assert "factur-x.xml" in _attachment_names(pdf)


def test_no_xml_means_no_claim_and_no_attachment():
    """The whole point: silence rather than an unverifiable assertion."""
    pdf = _render(b"")
    assert _CLAIM not in _text(pdf)
    assert "EN 16931" not in _text(pdf)
    assert "factur-x.xml" not in _attachment_names(pdf)


def test_no_attachment_is_written_that_cannot_be_parsed():
    """A `factur-x.xml` a consumer cannot parse is worse than none: it finds the
    attachment, tries to read it as CII, and fails — instead of simply seeing a
    plain PDF.

    Asserting merely that the payload is *truthy* is not enough, and this test
    says so because it learned it: the unfixed code attached `b"\\n"`, a single
    newline, which is truthy and is not XML. So every attachment written must
    actually parse.
    """
    for name, payload in (PdfReader(io.BytesIO(_render(b""))).attachments or {}).items():
        raw = payload[0] if isinstance(payload, list) else payload
        assert raw.strip(), f"attachment {name} is present but blank"
        ElementTree.fromstring(raw)  # must be real XML, not whitespace


def test_the_invoice_still_renders_its_own_content_without_xml():
    """Dropping the claim must not drop the document."""
    text = _text(_render(b""))
    assert "INV-TEST-0001" in text
    assert "Test Buyer OU" in text
    assert "122.00 EUR" in text  # 100.00 + 22% — the totals still compute
