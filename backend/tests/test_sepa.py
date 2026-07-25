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


async def _paid_run(auth_client, client, approver, *, vendor, number, price="100"):
    """A PAID run under the WO-9 flow: owner creates, `approver` approves the run,
    and a dedicated third member pays (maker ≠ checker ≠ payer)."""
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
    appr = await auth_client.post(
        f"/api/v1/payment-runs/{run['id']}/approve",
        headers=_h(approver),
        json={"version": run["version"]},
    )
    assert appr.status_code == 200, appr.text
    payer = await _member(auth_client, client, f"payer-{number.lower()}@acme.io", role="admin")
    paid = await auth_client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        headers=_h(payer),
        json={"version": appr.json()["version"], "reference": "SEPA-9"},
    )
    assert paid.status_code == 200, paid.text
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
    rid = await _paid_run(auth_client, client, approver, vendor="Acme Supplies", number="INV-1")
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
async def test_mixed_currency_run_is_refused_when_a_line_cannot_convert(auth_client, client):
    """WO-8: an invoice with no reliable EUR amount cannot enter a payment run —
    the refusal names the line and the missing rate's currency."""
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Warsaw Ltd",
            "invoice_number": "INV-XTS",
            "issue_date": "2026-05-01",
            "due_date": "2026-12-01",
            "currency": "XTS",  # no rate exists → total_eur is NULL, fx_source unknown
            "line_items": [
                {"description": "W", "quantity": "1", "unit_price": "1000", "tax_rate": "0"}
            ],
        },
    )
    inv = r.json()
    assert inv["total_eur"] is None and inv["fx_source"] == "unknown"
    iid = inv["id"]
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
    run = await auth_client.post("/api/v1/payment-runs", json={"invoice_ids": [iid]})
    assert run.status_code == 422, run.text
    detail = run.json()["detail"]
    assert "INV-XTS" in detail and "XTS" in detail


@pytest.mark.asyncio
async def test_sepa_never_labels_a_foreign_amount_eur(auth_client, client, db_session):
    """THE WO-8 regression: a foreign-currency invoice with a missing total_eur
    can NEVER produce a SEPA InstdAmt in the wrong currency. Legacy data is
    simulated by corrupting a paid run's invoice AFTER the run-creation gate:
    the export refuses outright — no XML, no `Ccy="EUR"` over 1,000 PLN."""
    from sqlalchemy import select, update

    from app.models.invoice import Invoice

    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    rid = await _paid_run(auth_client, client, approver, vendor="Acme Supplies", number="INV-OK")
    await _set_vendor_iban(auth_client, "Acme Supplies", "DE89370400440532013000", "COBADEFFXXX")

    # The healthy run: every emitted amount carries the currency it is actually
    # denominated in (EUR source → EUR label), never a relabelled foreign figure.
    good = await auth_client.get(f"/api/v1/payment-runs/{rid}/sepa")
    assert good.status_code == 200
    root = ET.fromstring(good.text)
    amts = root.findall(".//p:CdtTrfTxInf/p:Amt/p:InstdAmt", _NS)
    assert amts, "expected at least one credit transfer"
    for amt in amts:
        assert amt.get("Ccy") == "EUR"
        assert amt.text == "100.00"  # the invoice's EUR total, not a foreign figure

    # Corrupt the paid invoice the way pre-WO-8 data could look: foreign
    # currency, total 1000, no stamped EUR conversion.
    iid = await db_session.scalar(select(Invoice.id).where(Invoice.invoice_number == "INV-OK"))
    await db_session.execute(
        update(Invoice)
        .where(Invoice.id == iid)
        .values(currency="PLN", total=1000, total_eur=None, fx_source="unknown")
    )
    await db_session.commit()

    # WO-9 export-once: the healthy export above counted, so the re-export needs the
    # explicit confirm flag — the refusal we want here is the FX one, not 409.
    bad = await auth_client.get(f"/api/v1/payment-runs/{rid}/sepa?confirm_reexport=true")
    assert bad.status_code == 422, bad.text
    detail = bad.json()["detail"]
    assert "INV-OK" in detail and "PLN" in detail
    assert "<Document" not in bad.text  # NO payment file was produced at all


@pytest.mark.asyncio
async def test_sepa_422_when_no_creditor_iban(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    rid = await _paid_run(auth_client, client, approver, vendor="NoBank Ltd", number="INV-2")
    # Vendor has no IBAN → nothing to pay.
    r = await auth_client.get(f"/api/v1/payment-runs/{rid}/sepa")
    assert r.status_code == 422 and "supplier IBAN" in r.json()["detail"]


@pytest.mark.asyncio
async def test_sepa_422_when_issuer_has_no_iban(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    # No issuer profile set → no debtor IBAN.
    rid = await _paid_run(auth_client, client, approver, vendor="Acme Supplies", number="INV-3")
    await _set_vendor_iban(auth_client, "Acme Supplies", "DE89370400440532013000")
    r = await auth_client.get(f"/api/v1/payment-runs/{rid}/sepa")
    assert r.status_code == 422 and "IBAN" in r.json()["detail"]
