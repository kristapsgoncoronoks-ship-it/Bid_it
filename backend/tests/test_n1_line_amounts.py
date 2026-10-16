"""N1 captured money stays distinct from calculation and original evidence."""

import io
import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice
from app.services import extraction_provider, parser
from tests.test_einvoice import CII, UBL


def draft(**changes):
    return {
        "vendor_name": "Synthetic Supplier",
        "invoice_number": "LINE-1",
        "issue_date": "2026-10-13",
        "currency": "EUR",
        "line_items": [
            {
                "description": "Service",
                "quantity": "1",
                "unit_price": "100",
                "tax_rate": "21",
                **changes,
            }
        ],
    }


def parse_json(**changes):
    return parser.parse_invoice_file("invoice.json", json.dumps(draft(**changes)).encode())


def test_json_source_amount_aliases_and_no_defaulted_source():
    parsed = parse_json(tax_amount="21", line_gross="121")
    line = parsed.draft.line_items[0]
    assert line.captured_tax_amount == Decimal("21") and line.captured_gross_amount == Decimal(
        "121"
    )
    fields = {f.field: f for f in parsed.fields if f.line_index == 0}
    assert fields["captured_tax_amount"].original_value == "21"
    assert fields["captured_tax_amount"].status == "extracted"
    absent = parse_json()
    assert absent.draft.line_items[0].captured_tax_amount is None
    assert absent.draft.line_items[0].captured_gross_amount is None
    zero = parse_json(captured_tax_amount="0", captured_gross_amount="0")
    assert zero.draft.line_items[0].captured_tax_amount == 0
    assert any("differs" in w for w in zero.warnings)


@pytest.mark.parametrize("value", ["bad", "NaN", "Infinity", "-1", "0.001", "1000000000000"])
def test_invalid_source_amount_remains_missing_with_warning(value):
    parsed = parse_json(captured_tax_amount=value)
    assert parsed.draft.line_items[0].captured_tax_amount is None
    field = next(f for f in parsed.fields if f.field == "captured_tax_amount")
    assert field.status == "missing" and field.original_value == value
    assert any("invalid" in warning for warning in parsed.warnings)


def test_csv_optional_money_and_missing_fields_do_not_flag_every_capture():
    csv = "invoice_number,vendor_name,issue_date,description,amount,tax_rate,line_tax_amount,gross_amount\nL-1,Supplier,2026-10-13,Service,100,21,21,121\n"
    parsed = parser.parse_invoice_file("invoice.csv", csv.encode())
    assert parsed.draft.line_items[0].captured_gross_amount == Decimal("121")
    missing = extraction_provider.JsonProvider().extract(
        "invoice.json", json.dumps(draft()).encode()
    )
    for field in missing.fields:
        if field.field.startswith("captured_"):
            assert field.status == "missing" and field.low_confidence is False


def test_ubl_line_tax_scope_and_gross_are_never_inferred():
    base = parser.parse_invoice_file("invoice.xml", UBL.encode()).draft
    assert base.line_items[0].captured_tax_amount is None
    assert base.line_items[0].captured_gross_amount is None
    xml = UBL.replace(
        "<cac:InvoiceLine>",
        '<cac:InvoiceLine><cac:TaxTotal><cbc:TaxAmount currencyID="EUR">42</cbc:TaxAmount></cac:TaxTotal><cbc:TaxInclusiveAmount currencyID="EUR">242</cbc:TaxInclusiveAmount>',
        1,
    )
    parsed = parser.parse_invoice_file("invoice.xml", xml.encode())
    assert parsed.draft.line_items[0].captured_tax_amount == Decimal("42")
    assert parsed.draft.line_items[0].captured_gross_amount == Decimal("242")
    assert parsed.draft.line_items[1].captured_tax_amount is None
    wrong = parser.parse_invoice_file(
        "invoice.xml", xml.replace('currencyID="EUR">42', 'currencyID="USD">42').encode()
    )
    assert wrong.draft.line_items[0].captured_tax_amount is None
    assert any("currency differs" in w for w in wrong.warnings)


def test_cii_net_total_and_header_tax_are_not_line_capture():
    parsed = parser.parse_invoice_file("invoice.xml", CII.encode())
    assert parsed.draft.line_items[0].amount == Decimal("2400")
    assert parsed.draft.line_items[0].captured_gross_amount is None
    assert parsed.draft.line_items[0].captured_tax_amount is None  # header 480 is not line evidence
    xml = CII.replace(
        "<ram:RateApplicablePercent>20",
        "<ram:CalculatedAmount>480</ram:CalculatedAmount><ram:RateApplicablePercent>20",
    ).replace(
        "<ram:LineTotalAmount>2400.00</ram:LineTotalAmount>",
        "<ram:LineTotalAmount>2400.00</ram:LineTotalAmount><ram:GrandTotalAmount>2880</ram:GrandTotalAmount>",
    )
    line = parser.parse_invoice_file("invoice.xml", xml.encode()).draft.line_items[0]
    assert line.captured_tax_amount == Decimal("480") and line.captured_gross_amount == Decimal(
        "2880"
    )


async def test_save_review_correction_and_version_guards(auth_client, db_session):
    saved = await auth_client.post(
        "/api/v1/invoices", json=draft(captured_tax_amount="21", captured_gross_amount="125")
    )
    assert saved.status_code == 201, saved.text
    inv = saved.json()
    line = inv["line_items"][0]
    assert (line["tax_amount"], line["gross_amount"], line["captured_gross_amount"]) == (
        "21.00",
        "121.00",
        "125.00",
    )
    assert inv["total"] == "121.00" and line["source_amounts_match"] is False
    url = f"/api/v1/invoices/{inv['id']}"
    review = (await auth_client.get(url + "/review")).json()
    corrected = {
        **review["lines"][0],
        "tax_rate": "20",
        "captured_tax_amount": "20",
        "captured_gross_amount": "120",
    }
    body = {"version": review["version"], "lines": [corrected]}
    response = await auth_client.put(url + "/lines", json=body)
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["total"] == "120.00" and current["lines"][0]["gross_amount"] == "120.00"
    assert current["lines"][0]["source_amounts_match"] is True
    from app.models.audit import AuditEvent

    event = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.target_id == inv["id"], AuditEvent.action == "invoice.edit"
        )
    )
    meta = json.loads(event.meta)
    assert meta["captured_amounts_before"][0]["captured_gross_amount"] == "125.00"
    assert meta["captured_amounts_after"][0]["captured_gross_amount"] == "120.00"
    assert (await auth_client.put(url + "/lines", json=body)).status_code == 409
    # Existing workflow lock applies equally to newly captured source fields.
    from app.models.invoice import WorkflowState

    row = await db_session.get(Invoice, inv["id"])
    row.workflow_state = WorkflowState.approved
    await db_session.commit()
    blocked = await auth_client.put(url + "/lines", json={**body, "version": current["version"]})
    assert blocked.status_code == 409
    detail = (await auth_client.get(url)).json()["line_items"][0]
    assert detail["captured_gross_amount"] == "120.00"


async def test_exact_rounding_mixed_rates_and_explicit_clearing(auth_client):
    payload = draft(captured_tax_amount="21", captured_gross_amount="121")
    payload["line_items"] += [
        {
            "description": "Zero VAT",
            "amount": "100",
            "unit_price": "100",
            "tax_rate": "0",
            "captured_tax_amount": "0",
            "captured_gross_amount": "100",
        },
        {
            "description": "Rounding",
            "unit_price": "0.05",
            "tax_rate": "10",
            "captured_tax_amount": "0.01",
            "captured_gross_amount": "0.06",
        },
    ]
    response = await auth_client.post("/api/v1/invoices", json=payload)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["total"] == "221.06" and saved["tax_amount"] == "21.01"
    assert saved["line_items"][2]["tax_amount"] == "0.01"
    assert all(line["source_amounts_match"] for line in saved["line_items"])
    url = f"/api/v1/invoices/{saved['id']}"
    review = (await auth_client.get(url + "/review")).json()
    for line in review["lines"]:
        line["captured_tax_amount"] = None
        line["captured_gross_amount"] = None
    cleared = await auth_client.put(
        url + "/lines", json={"version": review["version"], "lines": review["lines"]}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["total"] == "221.06"
    assert all(
        line["source_amounts_match"] is None and line["captured_tax_amount"] is None
        for line in cleared.json()["lines"]
    )


async def test_capture_correction_keeps_original_provenance(auth_client, parse_upload):
    payload = draft(captured_tax_amount="25", captured_gross_amount="125")
    parsed = await parse_upload(
        auth_client,
        {"file": ("invoice.json", io.BytesIO(json.dumps(payload).encode()), "application/json")},
    )
    run_id = parsed["extraction_run_id"]
    response = await auth_client.post(
        f"/api/v1/invoices/captures/{run_id}/review",
        json={
            "fields": [{"field": "captured_tax_amount", "line_index": 0, "reviewed_value": "21"}]
        },
    )
    assert response.status_code == 200
    fields = (await auth_client.get(f"/api/v1/invoices/captures/{run_id}/fields")).json()
    tax = next(f for f in fields if f["field"] == "captured_tax_amount")
    assert tax["original_value"] == "25" and tax["reviewed_value"] == "21"
    assert tax["value"] == "25.00"


async def test_absent_source_and_historical_amounts_are_honest(auth_client, db_session):
    saved = (await auth_client.post("/api/v1/invoices", json=draft())).json()
    line = saved["line_items"][0]
    assert line["tax_amount"] == "21.00" and line["captured_tax_amount"] is None
    assert line["source_amounts_match"] is None
    row = await db_session.scalar(select(Invoice).where(Invoice.id == saved["id"]))
    await db_session.refresh(row, attribute_names=["line_items"])
    row.line_items[0].tax_amount = None
    row.line_items[0].gross_amount = None  # migration-compatible historical fixture
    await db_session.commit()
    historical = (await auth_client.get(f"/api/v1/invoices/{saved['id']}")).json()
    assert historical["line_items"][0]["tax_amount"] is None
    assert historical["total"] == "121.00"
