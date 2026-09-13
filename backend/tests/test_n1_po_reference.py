"""N1 — the received invoice keeps the supplier's purchase-order reference (BT-13).

`IssuedInvoice.po_reference` has carried EN-16931 BT-13 since the issuing module
shipped: the order reference a BUYER quotes so they can match our invoice to
their purchase order. The RECEIVING side had no such column, and `einvoice.py`
— which really does parse inbound UBL and CII — read straight past the element.
A supplier could send the field, in the standard, in the place the standard puts
it, and the number was discarded on arrival. Nothing downstream could match a
supplier invoice to a purchase order, which is the first thing an AP approver
asks for.

These tests pin the properties that make the capture trustworthy: it is read
from the RIGHT element in each syntax, it is never INVENTED when the document
does not carry one, it is visible where an approver works (the list, not only
the detail), and a reviewer can correct AND clear what capture read.
"""

from __future__ import annotations

import io

import pytest

from app.models.invoice import Invoice
from app.models.issued_invoice import IssuedInvoice
from app.services import einvoice, parser

# A UBL invoice that quotes the buyer's order in cac:OrderReference/cbc:ID —
# the only place UBL 2.1 puts BT-13.
UBL_WITH_PO = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>INV-UBL-9001</cbc:ID>
  <cbc:IssueDate>2026-06-02</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:OrderReference><cbc:ID>PO-2026-0447</cbc:ID></cac:OrderReference>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyLegalEntity><cbc:RegistrationName>Northwind Traders GmbH</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="EA">2</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">200.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>Managed database</cbc:Name>
      <cac:ClassifiedTaxCategory><cbc:Percent>21</cbc:Percent></cac:ClassifiedTaxCategory></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="EUR">100.00</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
</Invoice>"""

# The same document with the order reference simply absent — the common case.
UBL_WITHOUT_PO = UBL_WITH_PO.replace(
    "  <cac:OrderReference><cbc:ID>PO-2026-0447</cbc:ID></cac:OrderReference>\n", ""
)

# CII puts BT-13 on the agreement side, as the issuer-assigned id of the buyer's
# order document.
CII_WITH_PO = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
  xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument><ram:ID>INV-CII-9002</ram:ID>
    <ram:IssueDateTime><udt:DateTimeString format="102">20260602</udt:DateTimeString></ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:SpecifiedTradeProduct><ram:Name>Consulting day</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeAgreement>
        <ram:NetPriceProductTradePrice><ram:ChargeAmount>800.00</ram:ChargeAmount></ram:NetPriceProductTradePrice>
      </ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="DAY">1</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:ApplicableTradeTax><ram:RateApplicablePercent>20</ram:RateApplicablePercent></ram:ApplicableTradeTax>
        <ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>800.00</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>Globex Consulting SA</ram:Name></ram:SellerTradeParty>
      <ram:BuyerOrderReferencedDocument><ram:IssuerAssignedID>PO-2026-0912</ram:IssuerAssignedID></ram:BuyerOrderReferencedDocument>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:GrandTotalAmount>960.00</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""

CII_WITHOUT_PO = CII_WITH_PO.replace(
    "      <ram:BuyerOrderReferencedDocument><ram:IssuerAssignedID>PO-2026-0912</ram:IssuerAssignedID></ram:BuyerOrderReferencedDocument>\n",
    "",
)


# --------------------------------------------------------------------------- #
# capture — the two structured syntaxes
# --------------------------------------------------------------------------- #


def test_ubl_order_reference_is_captured():
    draft = einvoice.parse_xml_bytes(UBL_WITH_PO.encode(), "inv.xml").draft
    assert draft.po_reference == "PO-2026-0447"
    # And it is the ORDER reference, not the invoice's own id.
    assert draft.invoice_number == "INV-UBL-9001"


def test_ubl_without_an_order_reference_invents_nothing():
    """The failure mode worth guarding: `cbc:ID` appears all over a UBL
    document, and a looser search would file the invoice number — or a line
    number — as the invoice's own purchase order. An approver would then match
    against a PO that never existed."""
    draft = einvoice.parse_xml_bytes(UBL_WITHOUT_PO.encode(), "inv.xml").draft
    assert draft.po_reference is None
    assert draft.invoice_number == "INV-UBL-9001"


def test_cii_buyer_order_reference_is_captured():
    draft = einvoice.parse_xml_bytes(CII_WITH_PO.encode(), "inv.xml").draft
    assert draft.po_reference == "PO-2026-0912"
    assert draft.invoice_number == "INV-CII-9002"


def test_cii_without_a_buyer_order_reference_invents_nothing():
    draft = einvoice.parse_xml_bytes(CII_WITHOUT_PO.encode(), "inv.xml").draft
    assert draft.po_reference is None


def test_an_oversized_order_reference_does_not_fail_the_whole_intake():
    """BT-13 is free text in the standard and nobody is required to send it, so
    a supplier who puts 400 characters in the element must not cost us the
    invoice. Capture trims to the column width instead of raising."""
    xml = UBL_WITH_PO.replace("PO-2026-0447", "P" * 400)
    draft = einvoice.parse_xml_bytes(xml.encode(), "inv.xml").draft
    assert draft.po_reference == "P" * 60
    assert draft.invoice_number == "INV-UBL-9001", "the rest of the invoice is still read"


def test_an_empty_order_reference_element_is_absence_not_an_empty_string():
    """`<cac:OrderReference><cbc:ID/></cac:OrderReference>` is a supplier
    sending nothing. Storing `""` would make a queue filtered on 'has a PO
    reference' claim this invoice does."""
    xml = UBL_WITH_PO.replace("PO-2026-0447", "  ")
    assert einvoice.parse_xml_bytes(xml.encode(), "inv.xml").draft.po_reference is None


# --------------------------------------------------------------------------- #
# capture — the unstructured sources
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", parser.PO_REFERENCE_KEYS)
def test_a_json_source_may_spell_the_field_any_of_the_accepted_ways(key: str):
    """CSV and JSON come from spreadsheets and other people's exports, where
    there is no standard to appeal to. Each accepted spelling is read, and the
    provenance says the value was EXTRACTED rather than guessed."""
    import json

    payload = {
        "vendor_name": "Northwind Traders GmbH",
        "invoice_number": "INV-JSON-9003",
        "issue_date": "2026-06-02",
        key: "PO-2026-1155",
        "line_items": [{"description": "Rack unit", "quantity": "1", "unit_price": "10.00"}],
    }
    result = parser.parse_invoice_file("inv.json", json.dumps(payload).encode())
    assert result.draft.po_reference == "PO-2026-1155"

    row = next(p for p in result.fields if p.field == "po_reference")
    assert row.value == "PO-2026-1155"
    assert row.status == "extracted"


def test_a_source_with_no_po_reference_reports_it_missing_not_defaulted():
    """There is no sensible default for someone else's order number, so the
    provenance must say `missing` — the status a reviewer's queue uses to ask a
    human for the value, rather than `defaulted`, which means 'we filled it and
    the fill is fine'."""
    import json

    payload = {
        "vendor_name": "Northwind Traders GmbH",
        "invoice_number": "INV-JSON-9004",
        "issue_date": "2026-06-02",
        "line_items": [{"description": "Rack unit", "quantity": "1", "unit_price": "10.00"}],
    }
    result = parser.parse_invoice_file("inv.json", json.dumps(payload).encode())
    assert result.draft.po_reference is None

    row = next(p for p in result.fields if p.field == "po_reference")
    assert row.value is None
    assert row.status == "missing"


def test_a_csv_source_carries_the_po_reference_from_its_first_row():
    csv = (
        "vendor_name,invoice_number,issue_date,po_number,description,quantity,unit_price\n"
        "Northwind Traders GmbH,INV-CSV-9005,2026-06-02,PO-2026-2277,Rack unit,1,10.00\n"
    )
    result = parser.parse_invoice_file("inv.csv", io.BytesIO(csv.encode()).getvalue())
    assert result.draft.po_reference == "PO-2026-2277"


def test_an_over_long_po_reference_is_truncated_rather_than_rejected():
    """A 400-character 'PO number' in someone's export must not 500 the intake
    or fail schema validation on a field nobody is required to send. The column
    is 60 wide, so capture trims to fit — the same width the issued side uses,
    so a reference survives a round trip through either half of the product."""
    import json

    payload = {
        "vendor_name": "Northwind Traders GmbH",
        "invoice_number": "INV-JSON-9006",
        "issue_date": "2026-06-02",
        "po_reference": "P" * 400,
        "line_items": [{"description": "Rack unit", "quantity": "1", "unit_price": "10.00"}],
    }
    result = parser.parse_invoice_file("inv.json", json.dumps(payload).encode())
    assert result.draft.po_reference == "P" * 60


# --------------------------------------------------------------------------- #
# storage & the API
# --------------------------------------------------------------------------- #


def test_the_received_column_matches_the_issued_one():
    """The two sides are deliberately the same shape. If someone widens one,
    this asks them to widen the other — otherwise a PO reference this workspace
    accepts on the way in cannot be quoted on the way out."""
    received = Invoice.__table__.c.po_reference
    issued = IssuedInvoice.__table__.c.po_reference
    assert received.type.length == issued.type.length
    assert received.nullable, "existing invoices genuinely have no PO reference"


async def _invoice(auth_client, number: str, **extra) -> dict:
    body = {
        "vendor_name": "Northwind Traders GmbH",
        "invoice_number": number,
        "issue_date": "2026-06-02",
        "line_items": [
            {"description": "Rack unit", "quantity": "1", "unit_price": "10.00", "tax_rate": "0"}
        ],
        **extra,
    }
    r = await auth_client.post("/api/v1/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_the_po_reference_survives_the_round_trip_through_the_api(auth_client):
    inv = await _invoice(auth_client, "INV-API-9007", po_reference="PO-2026-3388")
    assert inv["po_reference"] == "PO-2026-3388"

    got = (await auth_client.get(f"/api/v1/invoices/{inv['id']}")).json()
    assert got["po_reference"] == "PO-2026-3388"


@pytest.mark.asyncio
async def test_the_po_reference_is_on_the_list_shape_an_approver_scans(auth_client):
    """Matching an invoice to a purchase order happens while scanning the
    queue. If the field only existed on the detail shape, the approver would
    have to open every row to do the one thing the field is for."""
    await _invoice(auth_client, "INV-API-9008", po_reference="PO-2026-4499")

    rows = (await auth_client.get("/api/v1/invoices")).json()
    items = rows["items"] if isinstance(rows, dict) else rows
    row = next(i for i in items if i["invoice_number"] == "INV-API-9008")
    assert row["po_reference"] == "PO-2026-4499"


@pytest.mark.asyncio
async def test_a_reviewer_can_correct_and_clear_what_capture_read(auth_client):
    """Capture reads a PO reference off a supplier's PDF, and capture is
    sometimes wrong. An absent field leaves the value alone; an explicit null
    CLEARS it — without that a mis-captured number would be permanent, and an
    approver would match against a purchase order that is not the one."""
    inv = await _invoice(auth_client, "INV-API-9009")
    assert inv["po_reference"] is None

    r = await auth_client.patch(
        f"/api/v1/invoices/{inv['id']}", json={"po_reference": "PO-2026-5500"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["po_reference"] == "PO-2026-5500"

    # An unrelated edit leaves it alone.
    r2 = await auth_client.patch(f"/api/v1/invoices/{inv['id']}", json={"notes": "checked"})
    assert r2.json()["po_reference"] == "PO-2026-5500"

    # An explicit null clears it.
    r3 = await auth_client.patch(f"/api/v1/invoices/{inv['id']}", json={"po_reference": None})
    assert r3.status_code == 200, r3.text
    assert r3.json()["po_reference"] is None


@pytest.mark.asyncio
async def test_an_over_long_po_reference_is_refused_at_the_api_edge(auth_client):
    """Capture trims, because it is reading someone else's file and must not
    fail the intake. A caller writing the field DIRECTLY is told it is too long
    rather than having 340 characters silently dropped."""
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Northwind Traders GmbH",
            "invoice_number": "INV-API-9010",
            "issue_date": "2026-06-02",
            "po_reference": "P" * 400,
            "line_items": [{"description": "Rack unit", "quantity": "1", "unit_price": "10.00"}],
        },
    )
    assert r.status_code == 422, r.text
