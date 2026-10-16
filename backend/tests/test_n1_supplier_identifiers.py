"""Supplier document identifiers survive capture without changing the vendor master."""

import json

import pytest

from app.services import parser
from tests.test_n1_po_reference import CII_WITH_PO, UBL_WITH_PO

FIELDS = ("supplier_registration_number", "supplier_vat_id")


def ubl() -> str:
    return UBL_WITH_PO.replace(
        "</cac:PartyLegalEntity>",
        "<cbc:CompanyID>REG-001</cbc:CompanyID></cac:PartyLegalEntity>"
        "<cac:PartyTaxScheme><cbc:CompanyID>FISCAL-999</cbc:CompanyID>"
        "<cac:TaxScheme><cbc:ID>OTH</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>"
        "<cac:PartyTaxScheme><cbc:CompanyID>VAT-002</cbc:CompanyID>"
        "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>",
    )


def cii() -> str:
    return CII_WITH_PO.replace(
        "</ram:SellerTradeParty>",
        "<ram:SpecifiedLegalOrganization><ram:ID>REG-001</ram:ID>"
        "</ram:SpecifiedLegalOrganization>"
        '<ram:SpecifiedTaxRegistration><ram:ID schemeID="FC">FISCAL-999</ram:ID>'
        "</ram:SpecifiedTaxRegistration>"
        '<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">VAT-002</ram:ID>'
        "</ram:SpecifiedTaxRegistration></ram:SellerTradeParty>",
    )


@pytest.mark.parametrize("xml", [ubl(), cii()])
def test_structured_capture_reads_the_seller_and_the_vat_scheme(xml):
    result = parser.parse_invoice_file("invoice.xml", xml.encode())
    assert result.draft.supplier_registration_number == "REG-001"
    assert result.draft.supplier_vat_id == "VAT-002"
    for field in FIELDS:
        provenance = next(row for row in result.fields if row.field == field)
        assert provenance.status == "extracted"
        assert provenance.value == getattr(result.draft, field)
        assert provenance.confidence is None


@pytest.mark.parametrize("xml", [UBL_WITH_PO, CII_WITH_PO])
def test_absent_seller_identifiers_are_not_invented_from_other_ids(xml):
    result = parser.parse_invoice_file("invoice.xml", xml.encode())
    for field in FIELDS:
        assert getattr(result.draft, field) is None
        assert next(row for row in result.fields if row.field == field).status == "missing"


@pytest.mark.parametrize("xml", [ubl(), cii()])
def test_a_fiscal_registration_is_not_a_vat_registration(xml):
    if "<Invoice " in xml:
        xml = xml.replace("<cbc:ID>VAT</cbc:ID>", "<cbc:ID>OTHER</cbc:ID>")
    else:
        xml = xml.replace('schemeID="VA"', 'schemeID="FC"')
    result = parser.parse_invoice_file("invoice.xml", xml.encode())
    assert result.draft.supplier_registration_number == "REG-001"
    assert result.draft.supplier_vat_id is None


@pytest.mark.parametrize("syntax", ["ubl", "cii"])
def test_buyer_identifiers_cannot_be_captured_as_seller_identifiers(syntax):
    xml = ubl() if syntax == "ubl" else cii()
    seller, buyer = (
        ("AccountingSupplierParty", "AccountingCustomerParty")
        if syntax == "ubl"
        else ("SellerTradeParty", "BuyerTradeParty")
    )
    xml = xml.replace(seller, buyer)
    result = parser.parse_invoice_file("invoice.xml", xml.encode())
    assert result.draft.supplier_registration_number is None
    assert result.draft.supplier_vat_id is None


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("suffix", ["json", "csv"])
def test_export_aliases_preserve_text_and_provenance(field, suffix):
    for key in parser.SUPPLIER_ID_KEYS[field]:
        if suffix == "json":
            content = json.dumps({key: "  000123-AB  ", "line_items": []}).encode()
        else:
            content = f"{key},description,unit_price\n  000123-AB  ,Service,10\n".encode()
        result = parser.parse_invoice_file(f"invoice.{suffix}", content)
        assert getattr(result.draft, field) == "000123-AB"
        row = next(row for row in result.fields if row.field == field)
        assert row.status == "extracted"
        assert row.value == "000123-AB"


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_blank_export_identifiers_are_missing(field, value):
    result = parser.parse_invoice_file("invoice.json", json.dumps({field: value}).encode())
    assert getattr(result.draft, field) is None
    assert next(row for row in result.fields if row.field == field).status == "missing"


@pytest.mark.parametrize("xml", [ubl(), cii()])
def test_oversized_identifiers_do_not_break_intake(xml):
    result = parser.parse_invoice_file(
        "invoice.xml", xml.replace("REG-001", "R" * 100).replace("VAT-002", "V" * 100).encode()
    )
    assert result.draft.supplier_registration_number == "R" * 80
    assert result.draft.supplier_vat_id == "V" * 80


async def test_confirm_list_review_correct_and_clear_without_vendor_changes(auth_client):
    draft = parser.parse_invoice_file("invoice.xml", ubl().encode()).draft.model_dump(mode="json")
    response = await auth_client.post("/api/v1/invoices", json=draft)
    assert response.status_code == 201, response.text
    invoice = response.json()
    for field, expected in zip(FIELDS, ("REG-001", "VAT-002"), strict=True):
        assert invoice[field] == expected
        detail = await auth_client.get(f"/api/v1/invoices/{invoice['id']}")
        assert detail.json()[field] == expected
    rows = (await auth_client.get("/api/v1/invoices")).json()["items"]
    assert rows[0]["supplier_vat_id"] == "VAT-002"
    vendors = await auth_client.get("/api/v1/vendors")
    assert vendors.status_code == 200, vendors.text
    vendor_before = next(v for v in vendors.json() if v["id"] == invoice["vendor_id"])
    url = f"/api/v1/invoices/{invoice['id']}/review"
    review = (await auth_client.get(url)).json()
    assert review["supplier_registration_number"] == "REG-001"
    response = await auth_client.patch(
        url, json={"version": review["version"], "supplier_vat_id": "VAT-CORRECTED"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["supplier_vat_id"] == "VAT-CORRECTED"
    assert response.json()["supplier_registration_number"] == "REG-001"
    stale = await auth_client.patch(
        url, json={"version": review["version"], "supplier_vat_id": "STALE"}
    )
    assert stale.status_code == 409
    response = await auth_client.patch(
        url, json={"version": response.json()["version"], "supplier_vat_id": None}
    )
    assert response.status_code == 200, response.text
    assert response.json()["supplier_vat_id"] is None
    vendor_after = next(
        v
        for v in (await auth_client.get("/api/v1/vendors")).json()
        if v["id"] == invoice["vendor_id"]
    )
    assert vendor_before == vendor_after


async def test_approved_identifiers_cannot_be_changed(auth_client, db_session):
    from app.models.invoice import Invoice, WorkflowState

    response = await auth_client.post(
        "/api/v1/invoices",
        json=parser.parse_invoice_file("invoice.xml", ubl().encode()).draft.model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    invoice = await db_session.get(Invoice, response.json()["id"])
    invoice.workflow_state = WorkflowState.approved
    await db_session.commit()
    response = await auth_client.patch(
        f"/api/v1/invoices/{invoice.id}/review",
        json={"version": invoice.version, "supplier_vat_id": "CHANGED"},
    )
    assert response.status_code == 409
    await db_session.refresh(invoice)
    assert invoice.supplier_vat_id == "VAT-002"


async def test_async_capture_keeps_original_and_human_correction(auth_client, parse_upload):
    import io

    parsed = await parse_upload(
        auth_client, {"file": ("invoice.xml", io.BytesIO(ubl().encode()), "application/xml")}
    )
    run_id = parsed["extraction_run_id"]
    url = f"/api/v1/invoices/captures/{run_id}"
    response = await auth_client.post(
        url + "/review",
        json={"fields": [{"field": "supplier_vat_id", "reviewed_value": "VAT-CORRECTED"}]},
    )
    assert response.status_code == 200, response.text
    fields = (await auth_client.get(url + "/fields")).json()
    vat = next(row for row in fields if row["field"] == "supplier_vat_id")
    assert vat["value"] == "VAT-002"
    assert vat["reviewed_value"] == "VAT-CORRECTED"
    draft = parsed["draft"]
    draft["supplier_vat_id"] = "VAT-CORRECTED"
    draft["extraction_run_id"] = run_id
    response = await auth_client.post("/api/v1/invoices", json=draft)
    assert response.status_code == 201, response.text
    assert response.json()["supplier_vat_id"] == "VAT-CORRECTED"
    assert response.json()["supplier_registration_number"] == "REG-001"
