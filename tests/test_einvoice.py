"""Structured e-invoice (UBL / CII / in-house XML) parsing — deterministic, no AI."""
import io
import zipfile

UBL = b"""<?xml version="1.0"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ID>INV-9001</cbc:ID><cbc:IssueDate>2026-05-31</cbc:IssueDate>
 <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party><cac:PartyLegalEntity>
   <cbc:RegistrationName>DKV Euro Service GmbH</cbc:RegistrationName></cac:PartyLegalEntity>
   <cac:PartyTaxScheme><cbc:CompanyID>SE502044770101</cbc:CompanyID></cac:PartyTaxScheme>
 </cac:Party></cac:AccountingSupplierParty>
 <cac:InvoiceLine><cbc:LineExtensionAmount currencyID="EUR">1000.00</cbc:LineExtensionAmount>
   <cac:Delivery><cac:DeliveryLocation><cac:Address><cac:Country>
     <cbc:IdentificationCode>SE</cbc:IdentificationCode></cac:Country></cac:Address>
   </cac:DeliveryLocation></cac:Delivery></cac:InvoiceLine>
 <cac:InvoiceLine><cbc:LineExtensionAmount>250.00</cbc:LineExtensionAmount>
   <cac:Delivery><cac:DeliveryLocation><cac:Address><cac:Country>
     <cbc:IdentificationCode>SE</cbc:IdentificationCode></cac:Country></cac:Address>
   </cac:DeliveryLocation></cac:Delivery></cac:InvoiceLine>
</Invoice>"""


def test_ubl_parsed():
    import extract as EX
    d = EX.extract(UBL, "invoice.xml")
    assert d["backend"] == "e-invoice" and d["confidence"] == "high"
    assert d["supplier"] == "DKV Euro Service GmbH"
    assert d["supplier_vat"] == "SE502044770101"
    assert d["statement_ref"] == "INV-9001"
    # both lines are Sweden -> one grouped claim line, net 1000+250
    assert len(d["lines"]) == 1
    line = d["lines"][0]
    assert line["country"] == "SE" and line["net"] == 1250.0 and line["currency"] == "EUR"
    # the XML itself is carried for vaulting on confirm
    assert d["_pdf_bytes"] and d["_pdf_bytes"][0][1] == UBL


def test_inhouse_xml_parsed():
    import extract as EX, os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = open(os.path.join(here, "demo_supplier_invoice.xml"), "rb").read()
    d = EX.extract(data, "demo_supplier_invoice.xml")
    assert d["statement_ref"] == "DEMO-2026-06-001"
    # 3 product lines, no per-line country -> one grouped row summing to the totals
    assert len(d["lines"]) == 1
    assert d["lines"][0]["net"] == 1693.42 and d["lines"][0]["vat"] == 355.62


def test_xml_inside_zip():
    import extract as EX
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.xml", UBL)
        z.writestr("b.xml", UBL)
    d = EX.extract(buf.getvalue(), "batch.zip")
    assert d["backend"] == "e-invoice"
    assert len(d["lines"]) == 2                       # one grouped line per XML
    assert len(d["files"]) == 2


def test_detects_xml_without_extension():
    import extract as EX
    assert EX._is_xml("weird", UBL) is True
    assert EX._is_xml("invoice.pdf", b"%PDF-1.4") is False
