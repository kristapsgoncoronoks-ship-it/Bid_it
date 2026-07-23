"""Reimbursement SEPA export (finish the expense payout story): employee bank
details + rendering a payout batch as an ISO 20022 pain.001 credit-transfer
(debtor = issuer, one creditor per employee-with-IBAN), with the same 422 guards
as the AP SEPA rail."""

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
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _activate(auth_client):
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    ).status_code == 200


async def _member(auth_client, client, email, name="Employee"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": "user"})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": name, "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _approved(auth_client, client, emp, title="Trip", amount="300.00"):
    r = await client.post(
        "/api/v1/expenses",
        headers=_h(emp),
        json={
            "title": title,
            "currency": "EUR",
            "items": [
                {
                    "spend_date": "2026-05-01",
                    "category": "travel",
                    "description": "Flight",
                    "amount": amount,
                    "vat_amount": "0",
                }
            ],
        },
    )
    rid = r.json()["id"]
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp))).json()
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Client meeting"},
            headers=_h(emp),
        )
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("receipt.png", _PNG, "image/png")},
            headers=_h(emp),
        )
    assert (await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))).status_code == 200
    assert (
        await auth_client.post(f"/api/v1/expenses/{rid}/decision", json={"action": "approve"})
    ).status_code == 200
    return rid


async def _batch(auth_client, report_ids):
    b = await auth_client.post("/api/v1/reimbursements", json={"report_ids": report_ids})
    assert b.status_code == 201, b.text
    return b.json()["id"]


@pytest.mark.asyncio
async def test_bank_details_roundtrip_normalises(client, auth_client):
    # A member sets their own IBAN (spaces stripped, upper-cased) and /me reflects it.
    emp = await _member(auth_client, client, "payee@corp.io")
    r = await client.put(
        "/api/v1/auth/bank-details",
        headers=_h(emp),
        json={"iban": "de89 3704 0044 0532 0130 00", "bic": "cobadeffxxx"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["iban"] == "DE89370400440532013000"
    me = await client.get("/api/v1/auth/me", headers=_h(emp))
    assert me.json()["user"]["bic"] == "COBADEFFXXX"


@pytest.mark.asyncio
async def test_reimbursement_sepa_export(auth_client, client):
    await _activate(auth_client)
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    emp = await _member(auth_client, client, "sepa-payee@corp.io", name="Jane Payee")
    await client.put(
        "/api/v1/auth/bank-details",
        headers=_h(emp),
        json={"iban": "DE89370400440532013000", "bic": "COBADEFFXXX"},
    )
    rid = await _approved(auth_client, client, emp, "Trip", "300.00")
    bid = await _batch(auth_client, [rid])

    resp = await auth_client.get(f"/api/v1/reimbursements/{bid}/sepa")
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Skipped"] == "0"
    root = ET.fromstring(resp.text)
    # Debtor IBAN = the issuer's.
    dbtr = root.find(".//p:PmtInf/p:DbtrAcct/p:Id/p:IBAN", _NS)
    assert dbtr is not None and dbtr.text == "NL91ABNA0417164300"
    # One credit transfer to the employee's IBAN for 300.00 EUR.
    txs = root.findall(".//p:CdtTrfTxInf", _NS)
    assert len(txs) == 1
    cdtr_iban = txs[0].find("./p:CdtrAcct/p:Id/p:IBAN", _NS)
    amt = txs[0].find("./p:Amt/p:InstdAmt", _NS)
    assert cdtr_iban.text == "DE89370400440532013000"
    assert amt.text == "300.00" and amt.get("Ccy") == "EUR"


@pytest.mark.asyncio
async def test_sepa_422_when_no_payee_has_iban(auth_client, client):
    await _activate(auth_client)
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    emp = await _member(auth_client, client, "noiban@corp.io")  # no bank details set
    rid = await _approved(auth_client, client, emp)
    bid = await _batch(auth_client, [rid])
    resp = await auth_client.get(f"/api/v1/reimbursements/{bid}/sepa")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sepa_422_when_issuer_has_no_iban(auth_client, client):
    await _activate(auth_client)  # no issuer profile → no debtor IBAN
    emp = await _member(auth_client, client, "hasiban@corp.io")
    await client.put(
        "/api/v1/auth/bank-details",
        headers=_h(emp),
        json={"iban": "DE89370400440532013000"},
    )
    rid = await _approved(auth_client, client, emp)
    bid = await _batch(auth_client, [rid])
    resp = await auth_client.get(f"/api/v1/reimbursements/{bid}/sepa")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sepa_export_requires_approver(auth_client, client):
    await _activate(auth_client)
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    emp = await _member(auth_client, client, "notapprover@corp.io")
    await client.put(
        "/api/v1/auth/bank-details", headers=_h(emp), json={"iban": "DE89370400440532013000"}
    )
    rid = await _approved(auth_client, client, emp)
    bid = await _batch(auth_client, [rid])
    # A plain member (not an expense approver) cannot export the payout file.
    resp = await client.get(f"/api/v1/reimbursements/{bid}/sepa", headers=_h(emp))
    assert resp.status_code == 403
