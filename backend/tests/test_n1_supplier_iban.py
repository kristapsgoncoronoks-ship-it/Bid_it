"""Captured bank instructions are private document evidence, never payment authority."""

import io
import json

import pytest
from sqlalchemy import select

from app.core import bank_id
from app.models.audit import AuditEvent
from app.models.capture_field_memory import CaptureFieldMemory
from app.models.vendor import Vendor
from app.services import capture_memory, extraction_provider, parser
from tests.test_einvoice import CII, UBL
from tests.test_n1_line_amounts import draft

# Public IBAN registry examples used only as synthetic test identifiers.
ONE = "DE89370400440532013000"
TWO = "GB29NWBK60161331926819"
SPACED = "de89 3704 0044 0532 0130 00"


def parsed(**fields):
    return parser.parse_invoice_file("invoice.json", json.dumps({**draft(), **fields}).encode())


@pytest.mark.parametrize("key", ["supplier_iban", "supplier_bank_iban", "payee_iban"])
def test_qualified_json_aliases_normalize_and_have_provenance(key):
    result = parsed(**{key: SPACED})
    assert result.draft.supplier_iban == ONE
    row = next(f for f in result.fields if f.field == "supplier_iban")
    assert row.status == "extracted" and row.value == ONE
    assert ONE not in repr(result.draft)


@pytest.mark.parametrize("fields", [{}, {"iban": ONE}, {"buyer_iban": ONE}, {"supplier_iban": ""}])
def test_absent_or_buyer_account_is_not_inferred(fields):
    result = parsed(**fields)
    assert result.draft.supplier_iban is None
    assert next(f for f in result.fields if f.field == "supplier_iban").status == "missing"


@pytest.mark.parametrize(
    "value", ["DE00370400440532013000", "not an account", "ZZ89370400440532013000", "A" * 501]
)
def test_invalid_source_is_missing_with_non_echoing_warning(value):
    result = parsed(supplier_iban=value)
    assert result.draft.supplier_iban is None
    assert any("Supplier IBAN is invalid" in warning for warning in result.warnings)
    assert all(value not in warning for warning in result.warnings)


def test_conflicting_aliases_and_csv_rows_refuse_account_selection():
    result = parsed(supplier_iban=ONE, payee_iban=TWO)
    assert result.draft.supplier_iban is None and any(
        "Multiple supplier" in w for w in result.warnings
    )
    content = f"vendor,invoice_number,issue_date,description,amount,supplier_iban\nSynthetic,CSV-1,2026-10-16,First,100,{ONE}\nSynthetic,CSV-1,2026-10-16,Second,50,{TWO}\n"
    result = parser.parse_invoice_file("invoice.csv", content.encode())
    assert result.draft.supplier_iban is None
    same = parser.parse_invoice_file("invoice.csv", content.replace(TWO, ONE).encode())
    assert same.draft.supplier_iban == ONE


def xml_with_accounts(kind, values, *, payer=False):
    if kind == "ubl":
        account = "PayerFinancialAccount" if payer else "PayeeFinancialAccount"
        block = "".join(
            f"<cac:PaymentMeans><cac:{account}><cbc:ID>{value}</cbc:ID></cac:{account}></cac:PaymentMeans>"
            for value in values
        )
        return UBL.replace("<cac:AccountingSupplierParty>", block + "<cac:AccountingSupplierParty>")
    account = "PayerPartyDebtorFinancialAccount" if payer else "PayeePartyCreditorFinancialAccount"
    block = "".join(
        f"<ram:SpecifiedTradeSettlementPaymentMeans><ram:{account}><ram:IBANID>{value}</ram:IBANID></ram:{account}></ram:SpecifiedTradeSettlementPaymentMeans>"
        for value in values
    )
    return CII.replace(
        "<ram:ApplicableHeaderTradeSettlement>", "<ram:ApplicableHeaderTradeSettlement>" + block
    )


@pytest.mark.parametrize("kind", ["ubl", "cii"])
@pytest.mark.parametrize(
    "values,payer,expected",
    [
        ([ONE], False, ONE),
        ([SPACED, ONE], False, ONE),
        ([ONE, TWO], False, None),
        (["invalid"], False, None),
        ([ONE], True, None),
    ],
)
def test_xml_payee_scope_ambiguity_and_provider_provenance(kind, values, payer, expected):
    content = xml_with_accounts(kind, values, payer=payer).encode()
    result = extraction_provider.run("invoice.xml", content)
    assert result.draft.supplier_iban == expected
    field = next(f for f in result.fields if f.field == "supplier_iban")
    assert field.status == ("extracted" if expected else "missing")
    assert field.value == expected


async def test_snapshot_review_clear_and_vendor_bank_approval_independence(
    auth_client, db_session, caplog
):
    vendor_response = await auth_client.post(
        "/api/v1/vendors", json={"name": "Synthetic Bank Supplier", "iban": ONE}
    )
    assert vendor_response.status_code == 201
    vendor = vendor_response.json()
    body = {**draft(), "vendor_id": vendor["id"], "supplier_iban": TWO}
    saved = await auth_client.post("/api/v1/invoices", json=body)
    assert saved.status_code == 201
    result = saved.json()
    assert result["supplier_iban_masked"] == bank_id.mask_iban(TWO)
    assert "supplier_iban" not in result and TWO not in saved.text
    url = f"/api/v1/invoices/{result['id']}"
    review = (await auth_client.get(url + "/review")).json()
    assert review["supplier_iban"] == TWO
    assert "supplier_iban" not in (await auth_client.get("/api/v1/invoices")).text
    invalid = await auth_client.patch(
        url + "/review",
        json={"version": review["version"], "supplier_iban": "invalid-private-account"},
    )
    assert invalid.status_code == 422 and "invalid-private-account" not in invalid.text
    correction = await auth_client.patch(
        url + "/review", json={"version": review["version"], "supplier_iban": SPACED}
    )
    assert correction.status_code == 200 and correction.json()["supplier_iban"] == ONE
    stale = await auth_client.patch(
        url + "/review", json={"version": review["version"], "supplier_iban": TWO}
    )
    assert stale.status_code == 409
    cleared = await auth_client.patch(
        url + "/review", json={"version": correction.json()["version"], "supplier_iban": None}
    )
    assert cleared.status_code == 200 and cleared.json()["supplier_iban"] is None
    stored_vendor = await db_session.get(Vendor, vendor["id"])
    await db_session.refresh(stored_vendor)
    assert stored_vendor.iban == ONE and stored_vendor.status == vendor["status"]
    audit_text = " ".join(
        event.meta or "" for event in await db_session.scalars(select(AuditEvent))
    )
    assert ONE not in audit_text and TWO not in audit_text
    assert ONE not in caplog.text and TWO not in caplog.text
    assert all(
        ONE not in str(record.__dict__) and TWO not in str(record.__dict__)
        for record in caplog.records
    )


async def test_capture_corrections_mask_audit_skip_memory_and_preserve_original(
    auth_client, parse_upload, db_session, caplog
):
    content = json.dumps({**draft(), "supplier_iban": ONE}).encode()
    upload = await parse_upload(
        auth_client, {"file": ("bank.json", io.BytesIO(content), "application/json")}
    )
    run = upload["extraction_run_id"]
    path = f"/api/v1/invoices/captures/{run}"
    corrected = await auth_client.post(
        path + "/review", json={"fields": [{"field": "supplier_iban", "reviewed_value": TWO}]}
    )
    assert corrected.status_code == 200
    row = next(f for f in corrected.json() if f["field"] == "supplier_iban")
    assert row["original_value"] == ONE and row["reviewed_value"] == TWO
    assert row["suggested_value"] is None
    replay = await auth_client.get(path + "/fields")
    assert next(f for f in replay.json() if f["field"] == "supplier_iban")["reviewed_value"] == TWO
    invalid = await auth_client.post(
        path + "/review",
        json={"fields": [{"field": "supplier_iban", "reviewed_value": "private-invalid-account"}]},
    )
    assert invalid.status_code == 422 and "private-invalid-account" not in invalid.text
    invalid_batch = await auth_client.post(
        path + "/review",
        json={
            "fields": [
                {"field": "vendor_name", "reviewed_value": "Uncommitted supplier correction"},
                {"field": "supplier_iban", "reviewed_value": "invalid-bank-account"},
            ]
        },
    )
    assert invalid_batch.status_code == 422
    unchanged = (await auth_client.get(path + "/fields")).json()
    assert next(f for f in unchanged if f["field"] == "vendor_name")["reviewed_value"] is None
    assert next(f for f in unchanged if f["field"] == "supplier_iban")["reviewed_value"] == TWO
    cleared = await auth_client.post(
        path + "/review", json={"fields": [{"field": "supplier_iban", "reviewed_value": ""}]}
    )
    assert cleared.status_code == 200
    row = next(f for f in cleared.json() if f["field"] == "supplier_iban")
    assert row["original_value"] == ONE and row["reviewed_value"] == ""
    memory = await db_session.scalar(
        select(CaptureFieldMemory).where(CaptureFieldMemory.field == "supplier_iban")
    )
    assert memory is None
    events = list(await db_session.scalars(select(AuditEvent).where(AuditEvent.target_id == run)))
    meta = json.dumps([json.loads(event.meta or "{}") for event in events], ensure_ascii=False)
    assert ONE not in meta and TWO not in meta
    assert bank_id.mask_iban(TWO) in meta
    assert ONE not in caplog.text and TWO not in caplog.text
    assert all(
        ONE not in str(record.__dict__) and TWO not in str(record.__dict__)
        for record in caplog.records
    )


async def test_sensitive_memory_is_refused_even_when_called_directly(auth_client, db_session):
    me = (await auth_client.get("/api/v1/auth/me")).json()
    org_id = me["organization"]["id"]
    await capture_memory.observe_fields(db_session, org_id, "Synthetic Supplier", ["supplier_iban"])
    await capture_memory.record_correction(
        db_session,
        org_id,
        vendor_name="Synthetic Supplier",
        field="supplier_iban",
        original_value=ONE,
        corrected_value=TWO,
    )
    assert (
        await capture_memory.hints_for(db_session, org_id, "Synthetic Supplier", ["supplier_iban"])
        == {}
    )
    assert (
        await db_session.scalar(
            select(CaptureFieldMemory).where(CaptureFieldMemory.field == "supplier_iban")
        )
        is None
    )


async def test_snapshot_is_tenant_scoped_and_locked_cannot_change(auth_client, client, db_session):
    from app.models.invoice import Invoice, WorkflowState

    saved = await auth_client.post("/api/v1/invoices", json={**draft(), "supplier_iban": ONE})
    assert saved.status_code == 201
    invoice_id = saved.json()["id"]
    invoice = await db_session.get(Invoice, invoice_id)
    invoice.workflow_state = WorkflowState.approved
    await db_session.commit()
    url = f"/api/v1/invoices/{invoice_id}"
    review = (await auth_client.get(url + "/review")).json()
    locked = await auth_client.patch(
        url + "/review", json={"version": review["version"], "supplier_iban": TWO}
    )
    assert locked.status_code == 409
    assert (await auth_client.get(url + "/review")).json()["supplier_iban"] == ONE
    analytics = await auth_client.get("/api/v1/analytics/summary")
    assert analytics.status_code == 200 and ONE not in analytics.text
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Synthetic Other Bank",
            "name": "Other Owner",
            "email": "other-bank@example.com",
            "password": "supersecret",
        },
    )
    assert registration.status_code == 201
    headers = {"Authorization": "Bearer " + registration.json()["token"]["access_token"]}
    for path in (url, url + "/review"):
        response = await client.get(path, headers=headers)
        assert response.status_code == 404 and ONE not in response.text
    denied = await client.patch(
        url + "/review", headers=headers, json={"version": review["version"], "supplier_iban": TWO}
    )
    assert denied.status_code == 404


@pytest.mark.parametrize("value", [123, [ONE], {"iban": ONE}])
async def test_non_text_input_rejected_without_echo(auth_client, value):
    response = await auth_client.post("/api/v1/invoices", json={**draft(), "supplier_iban": value})
    assert response.status_code == 422 and ONE not in response.text
