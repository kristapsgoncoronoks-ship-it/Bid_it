"""SEPA pain.001 payment file (Phase 17): vendor IBAN/BIC CRUD, and rendering a
paid payment run into an ISO 20022 credit-transfer XML (debtor = issuer, one
transaction per creditor-with-IBAN, control sum), with 422s when the issuer has no
IBAN or no invoice has a payable creditor."""

import xml.etree.ElementTree as ET

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
_NS = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"}


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _member(auth_client, client, email, role="admin"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _paid_run(auth_client, approver, *, vendor, number, price="100"):
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": vendor,
            "invoice_number": number,
            "issue_date": "2026-05-01",
            "due_date": "2026-12-01",
            "currency": "EUR",
            "line_items": [
                {"description": "W", "quantity": "1", "unit_price": price, "tax_rate": "0"}
            ],
        },
    )
    iid = r.json()["id"]
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    appr = await auth_client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(approver),
        json={"version": sub.json()["version"]},
    )
    await auth_client.post(
        f"/api/v1/invoices/{iid}/transition",
        json={"version": appr.json()["version"], "target": "scheduled_for_payment"},
    )
    run = (await auth_client.post("/api/v1/payment-runs", json={"invoice_ids": [iid]})).json()
    await auth_client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        json={"version": run["version"], "reference": "SEPA-9"},
    )
    return run["id"]


async def _set_vendor_iban(auth_client, name, iban, bic=None):
    vendors = (await auth_client.get("/api/v1/vendors")).json()
    vid = next(v["id"] for v in vendors if v["name"] == name)
    r = await auth_client.patch(f"/api/v1/vendors/{vid}", json={"iban": iban, "bic": bic})
    return r


@pytest.mark.asyncio
async def test_vendor_iban_crud(auth_client):
    v = await auth_client.post(
        "/api/v1/vendors", json={"name": "Bank Co", "iban": "de89 3704 0044 0532 0130 00"}
    )
    assert v.status_code == 201
    assert v.json()["iban"] == "DE89370400440532013000"  # spaces stripped, upper-cased
    upd = await auth_client.patch(f"/api/v1/vendors/{v.json()['id']}", json={"bic": "cobadeffxxx"})
    assert upd.status_code == 200 and upd.json()["bic"] == "COBADEFFXXX"


@pytest.mark.asyncio
async def test_sepa_export_structure(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    rid = await _paid_run(auth_client, approver, vendor="Acme Supplies", number="INV-1")
    await _set_vendor_iban(auth_client, "Acme Supplies", "DE89370400440532013000", "COBADEFFXXX")

    r = await auth_client.get(f"/api/v1/payment-runs/{rid}/sepa")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(r.text)
    assert root.tag == "{urn:iso:std:iso:20022:tech:xsd:pain.001.001.03}Document"
    # Debtor IBAN = the issuer's.
    dbtr_iban = root.find(".//p:PmtInf/p:DbtrAcct/p:Id/p:IBAN", _NS)
    assert dbtr_iban is not None and dbtr_iban.text == "NL91ABNA0417164300"
    # One credit-transfer to the creditor IBAN, control sum 100.00.
    txs = root.findall(".//p:CdtTrfTxInf", _NS)
    assert len(txs) == 1
    assert txs[0].find(".//p:CdtrAcct/p:Id/p:IBAN", _NS).text == "DE89370400440532013000"
    assert txs[0].find(".//p:Amt/p:InstdAmt", _NS).text == "100.00"
    assert root.find(".//p:GrpHdr/p:CtrlSum", _NS).text == "100.00"


@pytest.mark.asyncio
async def test_sepa_422_when_no_creditor_iban(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    rid = await _paid_run(auth_client, approver, vendor="NoBank Ltd", number="INV-2")
    # Vendor has no IBAN → nothing to pay.
    r = await auth_client.get(f"/api/v1/payment-runs/{rid}/sepa")
    assert r.status_code == 422 and "supplier IBAN" in r.json()["detail"]


@pytest.mark.asyncio
async def test_sepa_422_when_issuer_has_no_iban(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    # No issuer profile set → no debtor IBAN.
    rid = await _paid_run(auth_client, approver, vendor="Acme Supplies", number="INV-3")
    await _set_vendor_iban(auth_client, "Acme Supplies", "DE89370400440532013000")
    r = await auth_client.get(f"/api/v1/payment-runs/{rid}/sepa")
    assert r.status_code == 422 and "IBAN" in r.json()["detail"]
