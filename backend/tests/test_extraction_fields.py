"""Per-field capture provenance (Slice 5f + E1.2): the parsers record, per
header field AND per line-item field, whether it was extracted / defaulted /
missing; the provenance is stored against the run and surfaced on the extraction
endpoint. Line fields (`line_index` = n) carry the same honest confidence
semantics as the header: None = exact (deterministic source), 0.85 PDF
text-layer, 0.55 OCR."""

import io
import json
from decimal import Decimal

import pytest


def _prov(fields):
    # Header rows only (line_index None) — the five top-level fields.
    return {f["field"]: f["status"] for f in fields if f.get("line_index") is None}


def _line(fields, i):
    return {f["field"]: f for f in fields if f.get("line_index") == i}


@pytest.mark.asyncio
async def test_csv_provenance_extracted_defaulted_missing(auth_client, parse_upload):
    # invoice_number + issue_date present; vendor + currency + due_date absent.
    csv = "description,quantity,unit_price,tax_rate,invoice_number,issue_date\n"
    csv += "Fuel,10,1.50,21,INV-CSV-1,2026-02-01\n"
    files = {"file": ("lines.csv", io.BytesIO(csv.encode()), "text/csv")}
    up = await parse_upload(auth_client, files)
    p = _prov(up["fields"])
    assert p["invoice_number"] == "extracted"
    assert p["issue_date"] == "extracted"
    assert p["vendor_name"] == "missing"  # no vendor column
    assert p["due_date"] == "missing"  # no due_date
    assert p["currency"] == "defaulted"  # defaults to EUR


@pytest.mark.asyncio
async def test_json_provenance_and_surfaced_on_invoice(auth_client, parse_upload):
    payload = {
        "invoice_number": "J-1",
        "vendor_name": "AWS",
        "currency": "USD",
        "line_items": [
            {"description": "Compute", "quantity": "1", "unit_price": "100", "tax_rate": "0"}
        ],
    }  # issue_date + due_date absent
    files = {"file": ("inv.json", io.BytesIO(json.dumps(payload).encode()), "application/json")}
    up = await parse_upload(auth_client, files)
    p = _prov(up["fields"])
    assert p["vendor_name"] == "extracted" and p["currency"] == "extracted"
    assert p["issue_date"] == "defaulted"  # defaulted to today

    # Save, then the provenance is queryable per run on the extraction endpoint.
    saved = await auth_client.post("/api/v1/invoices", json=up["draft"])
    inv_id = saved.json()["id"]
    lineage = (await auth_client.get(f"/api/v1/invoices/{inv_id}/extraction")).json()
    assert len(lineage) == 1
    run = lineage[0]
    # 5 header rows (line_index None) + 6 line rows for the single line item
    # (E1.2 — the old `== 5` here encoded the header-only limitation).
    headers = [f for f in run["fields"] if f["line_index"] is None]
    lines = [f for f in run["fields"] if f["line_index"] == 0]
    assert len(headers) == 5 and len(lines) == 6
    assert len(run["fields"]) == 11
    by_field = {f["field"]: f for f in headers}
    assert by_field["vendor_name"]["value"] == "AWS"
    assert by_field["vendor_name"]["status"] == "extracted"
    assert by_field["currency"]["value"] == "USD"
    # Deterministic parser → no confidence score, on header AND line rows.
    assert all(f["confidence"] is None for f in run["fields"])


# --------------------------------------------------------------------------- #
# E1.2 — line-item provenance
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_csv_line_provenance_extracted_vs_defaulted(auth_client, parse_upload):
    # quantity/unit_price/description present per row; amount + category + tax_rate
    # absent → the fills are honestly labelled `defaulted` (incl. amount = qty×unit,
    # which the server recomputes identically on confirm).
    csv = "description,quantity,unit_price,invoice_number,vendor\n"
    csv += "Diesel,10,1.50,INV-L1,Acme\n"
    csv += "Toll,1,25.00,INV-L1,Acme\n"
    files = {"file": ("lines.csv", io.BytesIO(csv.encode()), "text/csv")}
    up = await parse_upload(auth_client, files)

    for i, desc in enumerate(["Diesel", "Toll"]):
        row = _line(up["fields"], i)
        assert set(row) == {
            "description",
            "category",
            "quantity",
            "unit_price",
            "amount",
            "tax_rate",
        }
        assert row["description"]["status"] == "extracted"
        assert row["description"]["value"] == desc
        assert row["quantity"]["status"] == "extracted"
        assert row["unit_price"]["status"] == "extracted"
        assert row["amount"]["status"] == "defaulted"  # computed qty × unit
        assert row["category"]["status"] == "defaulted"  # no category column
        assert row["tax_rate"]["status"] == "defaulted"  # no tax_rate column
        # Deterministic source → exact; and a deterministic `defaulted` line cell
        # does NOT flag (the line flag rule — unlike the header five).
        assert all(f["confidence"] is None for f in row.values())
        assert all(f["low_confidence"] is False for f in row.values())
    assert _line(up["fields"], 0)["amount"]["value"] == "15.00"


@pytest.mark.asyncio
async def test_json_line_provenance_and_lineage(auth_client, parse_upload):
    payload = {
        "invoice_number": "J-L2",
        "vendor_name": "AWS",
        "issue_date": "2026-06-01",
        "line_items": [
            {"description": "Compute", "quantity": "2", "unit_price": "100", "tax_rate": "21"},
            {"description": "Storage", "amount": "50.00"},
        ],
    }
    files = {"file": ("inv.json", io.BytesIO(json.dumps(payload).encode()), "application/json")}
    up = await parse_upload(auth_client, files)

    lines0 = _line(up["fields"], 0)
    lines1 = _line(up["fields"], 1)
    assert len(lines0) == 6 and len(lines1) == 6
    assert lines0["tax_rate"]["status"] == "extracted"
    assert lines0["tax_rate"]["value"] == "21"
    assert lines1["amount"]["status"] == "extracted"  # stated on the source row
    assert lines1["quantity"]["status"] == "defaulted"  # filled with 1
    assert lines1["unit_price"]["status"] == "defaulted"

    # The line rows survive save → lineage carries them with their line_index.
    saved = await auth_client.post("/api/v1/invoices", json=up["draft"])
    assert saved.status_code == 201, saved.text
    lineage = (await auth_client.get(f"/api/v1/invoices/{saved.json()['id']}/extraction")).json()
    indexes = sorted({f["line_index"] for f in lineage[0]["fields"] if f["line_index"] is not None})
    assert indexes == [0, 1]
    assert len(lineage[0]["fields"]) == 5 + 12


def test_line_confidence_per_method():
    """ARCH E1.2 acceptance, verbatim: a line-item field carries the same honest
    confidence semantics — None means exact (a typed field), not "unknown";
    PDF text 0.85; OCR 0.55 — with the 0.75 threshold flagging both sides."""
    from datetime import date

    from app.schemas.invoice import InvoiceCreate, LineItemIn
    from app.services.extraction_provider import _line_captures

    draft = InvoiceCreate(
        vendor_name="Acme",
        invoice_number="INV-M1",
        issue_date=date(2026, 6, 1),
        line_items=[
            LineItemIn(
                description="Diesel",
                quantity=Decimal("10"),
                unit_price=Decimal("1.50"),
                amount=Decimal("15.00"),
                tax_rate=Decimal("21"),
            )
        ],
    )

    def by_field(method):
        return {c.field: c for c in _line_captures(draft, method=method, provider="p")}

    text = by_field("text-layer")
    assert text["amount"].confidence == Decimal("0.85")
    assert text["amount"].low_confidence is False  # 0.85 ≥ 0.75

    ocr = by_field("ocr")
    assert ocr["amount"].confidence == Decimal("0.55")
    assert ocr["amount"].low_confidence is True  # 0.55 < 0.75
    assert ocr["quantity"].low_confidence is True

    xml = by_field("e-invoice-xml")
    assert xml["amount"].confidence is None  # exact — structured source
    assert xml["amount"].low_confidence is False
    # `category` is a structural fill for every draft-derived provider: defaulted,
    # no fake score, and (per the line flag rule) not flagged.
    for captures in (text, ocr, xml):
        assert captures["category"].status == "defaulted"
        assert captures["category"].confidence is None
        assert captures["category"].low_confidence is False
        assert captures["tax_rate"].status == "extracted"  # 21% was read
        assert all(c.line_index == 0 for c in captures.values())


_UBL_LINES = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>INV-UBL-E12</cbc:ID>
  <cbc:IssueDate>2026-06-01</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
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


@pytest.mark.asyncio
async def test_einvoice_upload_line_provenance(auth_client, parse_upload):
    files = {"file": ("inv.xml", io.BytesIO(_UBL_LINES.encode()), "application/xml")}
    up = await parse_upload(auth_client, files)
    assert up["method"] == "e-invoice-xml"

    row = _line(up["fields"], 0)
    assert len(row) == 6
    assert row["description"]["value"] == "Managed database"
    assert row["amount"]["value"] == "200.00"
    assert row["tax_rate"]["status"] == "extracted"
    # Structured XML → exact: no probabilistic score, nothing flagged.
    assert all(f["confidence"] is None for f in row.values())
    assert all(f["low_confidence"] is False for f in row.values())
    assert row["description"]["provider"] == "einvoice"
