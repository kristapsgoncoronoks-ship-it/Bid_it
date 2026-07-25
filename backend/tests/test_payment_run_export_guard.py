"""WO-9 — the bank-file export guard on payment runs (and the same rules on
reimbursement batches): PAYMENT_WRITE required, approved/paid runs only,
export-once with explicit re-export confirmation, a UNIQUE pain.001 MsgId per
generation (≤35 chars), skipped payees NAMED and acknowledged, every export
audited — and the five pre-existing double-payment guards still hold."""

import inspect
import json
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent

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


def _msg_id(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    el = root.find(".//p:GrpHdr/p:MsgId", _NS)
    assert el is not None and el.text
    return el.text


async def _member(auth_client, client, email, role="admin", name="M"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": name, "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _make_invoice(auth_client, number, vendor="Acme Supplies", unit_price="100"):
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": vendor,
            "invoice_number": number,
            "issue_date": "2026-05-01",
            "due_date": "2026-12-01",
            "currency": "EUR",
            "line_items": [
                {"description": "W", "quantity": "1", "unit_price": unit_price, "tax_rate": "0"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _schedule(auth_client, approver, iid):
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    appr = await auth_client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(approver),
        json={"version": sub.json()["version"]},
    )
    sch = await auth_client.post(
        f"/api/v1/invoices/{iid}/transition",
        json={"version": appr.json()["version"], "target": "scheduled_for_payment"},
    )
    assert sch.status_code == 200, sch.text


async def _set_vendor_iban(auth_client, name, iban, bic=None):
    vendors = (await auth_client.get("/api/v1/vendors")).json()
    vid = next(v["id"] for v in vendors if v["name"] == name)
    r = await auth_client.patch(f"/api/v1/vendors/{vid}", json={"iban": iban, "bic": bic})
    assert r.status_code == 200, r.text


async def _run(auth_client, client, approver, invoice_ids, *, approve=True):
    run = await auth_client.post("/api/v1/payment-runs", json={"invoice_ids": invoice_ids})
    assert run.status_code == 201, run.text
    body = run.json()
    if approve:
        appr = await client.post(
            f"/api/v1/payment-runs/{body['id']}/approve",
            headers=_h(approver),
            json={"version": body["version"]},
        )
        assert appr.status_code == 200, appr.text
        body = appr.json()
    return body


async def _approved_run(auth_client, client, approver, *, number="INV-EXP", with_iban=True):
    iid = await _make_invoice(auth_client, number)
    await _schedule(auth_client, approver, iid)
    if with_iban:
        await _set_vendor_iban(
            auth_client, "Acme Supplies", "DE89370400440532013000", "COBADEFFXXX"
        )
    return await _run(auth_client, client, approver, [iid])


@pytest.mark.asyncio
async def test_export_requires_payment_write(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    run = await _approved_run(auth_client, client, approver)
    # READ_ONLY holds PAYMENT_READ but not PAYMENT_WRITE → 403 on BOTH bank files.
    ro = await _member(auth_client, client, "ro@corp.io", role="user_free")
    assert (
        await client.get(f"/api/v1/payment-runs/{run['id']}", headers=_h(ro))
    ).status_code == 200
    for path in ("export", "sepa"):
        r = await client.get(f"/api/v1/payment-runs/{run['id']}/{path}", headers=_h(ro))
        assert r.status_code == 403, f"{path}: {r.status_code}"


@pytest.mark.asyncio
async def test_export_refused_on_an_unapproved_run(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    iid = await _make_invoice(auth_client, "INV-OPEN")
    await _schedule(auth_client, approver, iid)
    await _set_vendor_iban(auth_client, "Acme Supplies", "DE89370400440532013000")
    run = await _run(auth_client, client, approver, [iid], approve=False)
    for path in ("export", "sepa"):
        r = await auth_client.get(f"/api/v1/payment-runs/{run['id']}/{path}")
        assert r.status_code == 409, f"{path}: {r.status_code} {r.text}"
        assert r.json()["code"] == "run_not_exportable"


@pytest.mark.asyncio
async def test_second_export_requires_confirmation(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    run = await _approved_run(auth_client, client, approver)

    first = await auth_client.get(f"/api/v1/payment-runs/{run['id']}/sepa")
    assert first.status_code == 200, first.text

    again = await auth_client.get(f"/api/v1/payment-runs/{run['id']}/sepa")
    assert again.status_code == 409, again.text
    body = again.json()
    assert body["code"] == "already_exported"
    # The refusal carries the FIRST export's timestamp.
    detail = (await auth_client.get(f"/api/v1/payment-runs/{run['id']}")).json()
    assert detail["export_count"] == 1 and detail["exported_at"]
    assert detail["exported_at"][:19] in body["detail"]

    confirmed = await auth_client.get(
        f"/api/v1/payment-runs/{run['id']}/sepa?confirm_reexport=true"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert (await auth_client.get(f"/api/v1/payment-runs/{run['id']}")).json()["export_count"] == 2


@pytest.mark.asyncio
async def test_two_exports_produce_distinct_msg_ids(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    run = await _approved_run(auth_client, client, approver)

    one = await auth_client.get(f"/api/v1/payment-runs/{run['id']}/sepa")
    two = await auth_client.get(f"/api/v1/payment-runs/{run['id']}/sepa?confirm_reexport=true")
    assert one.status_code == 200 and two.status_code == 200
    m1, m2 = _msg_id(one.text), _msg_id(two.text)
    assert m1 != m2
    assert len(m1) <= 35 and len(m2) <= 35
    # Traceable back to the run, and the LAST one is stored on it.
    assert m1.startswith(f"RUN-{run['id'][:8]}-1-") and m2.startswith(f"RUN-{run['id'][:8]}-2-")
    assert (await auth_client.get(f"/api/v1/payment-runs/{run['id']}")).json()["last_msg_id"] == m2


@pytest.mark.asyncio
async def test_export_is_audited_with_msgid_and_totals(auth_client, client, db_session):
    approver = await _member(auth_client, client, "appr@acme.io")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    run = await _approved_run(auth_client, client, approver)
    r = await auth_client.get(f"/api/v1/payment-runs/{run['id']}/sepa")
    assert r.status_code == 200
    ev = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "payment_run.exported", AuditEvent.target_id == run["id"])
        .order_by(AuditEvent.seq.desc())
    )
    assert ev is not None and ev.actor_email == "owner@acme.io"
    meta = json.loads(ev.meta)
    assert meta["format"] == "sepa" and meta["export_count"] == 1
    assert meta["msg_id"] == _msg_id(r.text)
    assert meta["payees"] == 1 and meta["skipped"] == []
    assert meta["total_eur"] == "100.00"


@pytest.mark.asyncio
async def test_export_blocked_when_a_payee_has_no_iban(auth_client, client, db_session):
    approver = await _member(auth_client, client, "appr@acme.io")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    a = await _make_invoice(auth_client, "INV-A", vendor="HasBank BV")
    b = await _make_invoice(auth_client, "INV-B", vendor="NoBank Ltd")
    await _schedule(auth_client, approver, a)
    await _schedule(auth_client, approver, b)
    await _set_vendor_iban(auth_client, "HasBank BV", "DE89370400440532013000")
    run = await _run(auth_client, client, approver, [a, b])

    refused = await auth_client.get(f"/api/v1/payment-runs/{run['id']}/sepa")
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "skipped_payees"
    assert "NoBank Ltd" in body["detail"]  # the payee is NAMED, not counted in a header
    # A refusal is NOT an export — the counter must not move.
    assert (await auth_client.get(f"/api/v1/payment-runs/{run['id']}")).json()["export_count"] == 0

    ok = await auth_client.get(f"/api/v1/payment-runs/{run['id']}/sepa?acknowledge_skipped=true")
    assert ok.status_code == 200, ok.text
    root = ET.fromstring(ok.text)
    assert len(root.findall(".//p:CdtTrfTxInf", _NS)) == 1  # only the payable creditor
    ev = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "payment_run.exported", AuditEvent.target_id == run["id"])
        .order_by(AuditEvent.seq.desc())
    )
    assert json.loads(ev.meta)["skipped"] == ["NoBank Ltd"]


@pytest.mark.asyncio
async def test_reimbursement_batch_applies_the_same_rules(auth_client, client, db_session):
    """Same treatment for the employee payout rail: skipped payees NAMED and
    acknowledged, export-once with confirmation, distinct ≤35-char MsgIds, audit."""
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    ).status_code == 200
    await auth_client.put("/api/v1/issuer", json=ISSUER)

    async def employee(email, name):
        return await _member(auth_client, client, email, role="user", name=name)

    async def approved_report(emp, title):
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
                        "amount": "100.00",
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
        assert (
            await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
        ).status_code == 200
        assert (
            await auth_client.post(f"/api/v1/expenses/{rid}/decision", json={"action": "approve"})
        ).status_code == 200
        return rid

    has_iban = await employee("banked@corp.io", "Jane Banked")
    no_iban = await employee("unbanked@corp.io", "Joe Unbanked")
    await client.put(
        "/api/v1/auth/bank-details",
        headers=_h(has_iban),
        json={"iban": "DE89370400440532013000", "bic": "COBADEFFXXX"},
    )
    r1 = await approved_report(has_iban, "Banked trip")
    r2 = await approved_report(no_iban, "Unbanked trip")
    batch = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [r1, r2]})
    assert batch.status_code == 201, batch.text
    bid = batch.json()["id"]

    # Skipped employee NAMED; refusal is not an export.
    refused = await auth_client.get(f"/api/v1/reimbursements/{bid}/sepa")
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "skipped_payees"
    assert "Joe Unbanked" in refused.json()["detail"]  # the employee is NAMED
    assert "Jane Banked" not in refused.json()["detail"]

    ok = await auth_client.get(f"/api/v1/reimbursements/{bid}/sepa?acknowledge_skipped=true")
    assert ok.status_code == 200, ok.text
    assert ok.headers["X-Skipped"] == "1"  # compat header still served
    m1 = _msg_id(ok.text)

    # Export-once + a distinct MsgId on the confirmed re-export.
    again = await auth_client.get(f"/api/v1/reimbursements/{bid}/sepa?acknowledge_skipped=true")
    assert again.status_code == 409 and again.json()["code"] == "already_exported"
    two = await auth_client.get(
        f"/api/v1/reimbursements/{bid}/sepa?acknowledge_skipped=true&confirm_reexport=true"
    )
    assert two.status_code == 200, two.text
    m2 = _msg_id(two.text)
    assert m1 != m2 and len(m1) <= 35 and len(m2) <= 35
    assert m1.startswith(f"REIMB-{bid[:8]}-1-") and m2.startswith(f"REIMB-{bid[:8]}-2-")

    ev = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "expense.reimbursement_exported", AuditEvent.target_id == bid)
        .order_by(AuditEvent.seq.desc())
    )
    assert ev is not None
    meta = json.loads(ev.meta)
    assert meta["msg_id"] == m2 and meta["export_count"] == 2 and len(meta["skipped"]) == 1

    # A cancelled batch can never produce a bank file.
    b2 = await auth_client.post("/api/v1/reimbursements", json={"report_ids": []})
    assert b2.status_code == 422  # empty batch refused — build a real one to cancel
    r3 = await approved_report(has_iban, "Cancelled trip")
    b3 = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [r3]})).json()
    assert (await auth_client.delete(f"/api/v1/reimbursements/{b3['id']}")).status_code == 204
    dead = await auth_client.get(f"/api/v1/reimbursements/{b3['id']}/export")
    assert dead.status_code == 409 and dead.json()["code"] == "batch_not_exportable"


@pytest.mark.asyncio
async def test_existing_double_payment_guards_still_hold(auth_client, client):
    """WO-9 must not weaken the five pre-existing double-payment guards:
    1. pool exclusion — a run-linked invoice leaves the payable pool;
    2. create-time check — an invoice already in a run is refused from another;
    3. single-shot state gate — a paid run cannot be paid again;
    4. optimistic `version` — a stale pay is a 409;
    5. `SELECT … FOR UPDATE` — the pay/cancel/export loads still take the row lock."""
    approver = await _member(auth_client, client, "appr@acme.io")
    payer = await _member(auth_client, client, "payer@acme.io")
    iid = await _make_invoice(auth_client, "INV-G1")
    await _schedule(auth_client, approver, iid)
    run = await _run(auth_client, client, approver, [iid], approve=False)

    # (1) pool exclusion.
    payable = (await auth_client.get("/api/v1/payment-runs/payable")).json()
    assert all(i["id"] != iid for i in payable)
    # (2) create-time double-run check.
    dup = await auth_client.post("/api/v1/payment-runs", json={"invoice_ids": [iid]})
    assert dup.status_code == 422 and "already in a run" in dup.json()["detail"]
    # (4) stale version.
    appr = await client.post(
        f"/api/v1/payment-runs/{run['id']}/approve",
        headers=_h(approver),
        json={"version": run["version"]},
    )
    assert appr.status_code == 200
    stale = await client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        headers=_h(payer),
        json={"version": run["version"]},  # pre-approval version — stale
    )
    assert stale.status_code == 409 and "changed" in stale.json()["detail"]
    # (3) single-shot state gate: pay once, second pay refused.
    ok = await client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        headers=_h(payer),
        json={"version": appr.json()["version"], "reference": "SEPA-1"},
    )
    assert ok.status_code == 200, ok.text
    twice = await client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        headers=_h(payer),
        json={"version": ok.json()["version"]},
    )
    assert twice.status_code == 409 and "paid" in twice.json()["detail"]
    # (5) the FOR UPDATE row-lock path is still what pay/cancel/export use.
    from app.api.routes import payment_runs as pr_routes

    src = inspect.getsource(pr_routes)
    assert "with_for_update" in inspect.getsource(pr_routes._load)
    for call in ("run_id, lock=True", "lock=True)"):
        assert call in src  # the mutating loads pass lock=True
