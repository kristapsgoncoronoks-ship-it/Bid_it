"""Per-field capture provenance (Slice 5f): the CSV/JSON parsers record, per
top-level field, whether it was extracted / defaulted / missing; the provenance
is stored against the run and surfaced on the extraction endpoint."""

import io
import json

import pytest


def _prov(fields):
    return {f["field"]: f["status"] for f in fields}


@pytest.mark.asyncio
async def test_csv_provenance_extracted_defaulted_missing(auth_client):
    # invoice_number + issue_date present; vendor + currency + due_date absent.
    csv = "description,quantity,unit_price,tax_rate,invoice_number,issue_date\n"
    csv += "Fuel,10,1.50,21,INV-CSV-1,2026-02-01\n"
    files = {"file": ("lines.csv", io.BytesIO(csv.encode()), "text/csv")}
    up = (await auth_client.post("/api/v1/invoices/upload", files=files)).json()
    p = _prov(up["fields"])
    assert p["invoice_number"] == "extracted"
    assert p["issue_date"] == "extracted"
    assert p["vendor_name"] == "missing"  # no vendor column
    assert p["due_date"] == "missing"  # no due_date
    assert p["currency"] == "defaulted"  # defaults to EUR


@pytest.mark.asyncio
async def test_json_provenance_and_surfaced_on_invoice(auth_client):
    payload = {
        "invoice_number": "J-1",
        "vendor_name": "AWS",
        "currency": "USD",
        "line_items": [
            {"description": "Compute", "quantity": "1", "unit_price": "100", "tax_rate": "0"}
        ],
    }  # issue_date + due_date absent
    files = {"file": ("inv.json", io.BytesIO(json.dumps(payload).encode()), "application/json")}
    up = (await auth_client.post("/api/v1/invoices/upload", files=files)).json()
    p = _prov(up["fields"])
    assert p["vendor_name"] == "extracted" and p["currency"] == "extracted"
    assert p["issue_date"] == "defaulted"  # defaulted to today

    # Save, then the provenance is queryable per run on the extraction endpoint.
    saved = await auth_client.post("/api/v1/invoices", json=up["draft"])
    inv_id = saved.json()["id"]
    lineage = (await auth_client.get(f"/api/v1/invoices/{inv_id}/extraction")).json()
    assert len(lineage) == 1
    run = lineage[0]
    assert len(run["fields"]) == 5
    by_field = {f["field"]: f for f in run["fields"]}
    assert by_field["vendor_name"]["value"] == "AWS"
    assert by_field["vendor_name"]["status"] == "extracted"
    assert by_field["currency"]["value"] == "USD"
    # Deterministic parser → no confidence score.
    assert all(f["confidence"] is None for f in run["fields"])
