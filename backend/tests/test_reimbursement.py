"""Expense reimbursement payout batches (Phase 09): group approved reports, pay
them together (→ reimbursed + payment metadata), export a bank/payroll CSV, with
authorization + tenant isolation + optimistic concurrency."""

import csv
import io
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.expense import ExpenseReport, ReimbursementBatch
from app.models.job import Job
from app.models.webhook import WebhookDelivery
from app.services import audit

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


async def _member(auth_client, client, email, role="user", name="Employee"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": name, "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


def _payload(title="Trip", amount="300.00"):
    return {
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
    }


async def _draft(client, emp, title="Trip", amount="300.00"):
    r = await client.post("/api/v1/expenses", json=_payload(title, amount), headers=_h(emp))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _complete(client, rid, emp):
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


async def _approved(auth_client, client, emp, title="Trip", amount="300.00"):
    """A fully approved report ready for reimbursement."""
    rid = await _draft(client, emp, title, amount)
    await _complete(client, rid, emp)
    assert (await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))).status_code == 200
    dec = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision", json={"action": "approve", "version": 1}
    )
    assert dec.status_code == 200 and dec.json()["status"] == "approved"
    return rid


async def _payout_snapshot(session_factory, org_id, batch_id, report_id):
    """Read committed rows afresh, including fields the detail API omits."""
    async with session_factory() as db:
        snapshot = {}
        for label, model, predicates in (
            (
                "batch",
                ReimbursementBatch,
                (ReimbursementBatch.org_id == org_id, ReimbursementBatch.id == batch_id),
            ),
            (
                "report",
                ExpenseReport,
                (ExpenseReport.org_id == org_id, ExpenseReport.id == report_id),
            ),
            (
                "audit",
                AuditEvent,
                (AuditEvent.org_id == org_id, AuditEvent.target_id == batch_id),
            ),
            ("deliveries", WebhookDelivery, (WebhookDelivery.org_id == org_id,)),
            ("jobs", Job, (Job.org_id == org_id,)),
        ):
            rows = await db.execute(select(model.__table__).where(*predicates).order_by(model.id))
            snapshot[label] = [dict(row) for row in rows.mappings()]
        return snapshot


@pytest.mark.asyncio
async def test_batch_pay_marks_reports_reimbursed(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e1@corp.io")
    r1 = await _approved(auth_client, client, emp, "A", "100.00")
    r2 = await _approved(auth_client, client, emp, "B", "200.00")

    # Owner (an approver) groups both into a payout batch.
    batch = await auth_client.post(
        "/api/v1/reimbursements", json={"report_ids": [r1, r2], "method": "bank_transfer"}
    )
    assert batch.status_code == 201, batch.text
    b = batch.json()
    assert b["status"] == "open" and b["report_count"] == 2 and b["total_eur"] == "300.00"

    # Board R6: the batch's creator (the owner) may not also be its payer — a
    # second approver pays it. Both to be its checker.
    payer = await _member(auth_client, client, "payer1@corp.io", role="admin")
    paid = await client.post(
        f"/api/v1/reimbursements/{b['id']}/pay",
        headers=_h(payer),
        json={"version": b["version"], "reference": "PAY-2026-001"},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"
    for rep in paid.json()["reports"]:
        assert rep["status"] == "reimbursed" and rep["payment_reference"] == "PAY-2026-001"

    # And it's reflected on the report itself.
    got = (await auth_client.get(f"/api/v1/expenses/{r1}")).json()
    assert got["status"] == "reimbursed"


@pytest.mark.asyncio
async def test_only_approved_reports_can_be_batched(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e2@corp.io")
    draft = await _draft(client, emp)  # still draft
    r = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [draft]})
    assert r.status_code == 422 and "approved" in r.json()["detail"]


@pytest.mark.asyncio
async def test_report_cannot_be_in_two_batches(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e3@corp.io")
    rid = await _approved(auth_client, client, emp)
    first = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert first.status_code == 201
    again = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert again.status_code == 422 and "already in a payout batch" in again.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_open_batch_unlinks_reports(auth_client, client, _db):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e4@corp.io")
    rid = await _approved(auth_client, client, emp)
    b = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})).json()
    org_id = (await auth_client.get("/api/v1/auth/me")).json()["user"]["org_id"]
    before = await _payout_snapshot(_db, org_id, b["id"], rid)
    assert before["batch"][0]["export_count"] == 0
    cancel = await auth_client.delete(f"/api/v1/reimbursements/{b['id']}")
    assert cancel.status_code == 204
    after = await _payout_snapshot(_db, org_id, b["id"], rid)
    assert after["batch"][0]["status"] == "cancelled"
    assert after["batch"][0]["version"] == b["version"] + 1
    assert after["report"][0]["payout_batch_id"] is None
    assert after["report"][0]["status"] == before["report"][0]["status"]
    assert after["report"][0]["payment_reference"] is None
    assert sum(row["action"] == audit.A.REIMBURSE_CANCEL for row in after["audit"]) == 1
    # The report is payable again (approved, unbatched) → can go into a new batch.
    b2 = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert b2.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("export_route", ["export", "sepa"], ids=["csv", "sepa"])
async def test_committed_export_prevents_cancel_without_writes(
    auth_client, client, _db, export_route
):
    """An issued instruction must keep its report links until reconciliation."""
    await _activate(auth_client)
    emp = await _member(auth_client, client, "exported-payee@corp.io")
    if export_route == "sepa":
        issuer = await auth_client.put(
            "/api/v1/issuer",
            json={
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
            },
        )
        assert issuer.status_code == 200, issuer.text
        bank = await client.put(
            "/api/v1/auth/bank-details",
            headers=_h(emp),
            json={"iban": "DE89370400440532013000", "bic": "COBADEFFXXX"},
        )
        assert bank.status_code == 200, bank.text
    rid = await _approved(auth_client, client, emp, "Synthetic export guard", "150.00")
    # Never run a worker: this endpoint only proves durable queue rows exist.
    hook = await auth_client.post(
        "/api/v1/webhooks", json={"url": "https://example.test/payouts", "events": "*"}
    )
    assert hook.status_code == 201, hook.text
    created = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert created.status_code == 201, created.text
    batch = created.json()
    bid = batch["id"]
    exported = await auth_client.post(f"/api/v1/reimbursements/{bid}/{export_route}")
    assert exported.status_code == 200, exported.text
    assert ("text/csv" if export_route == "export" else "application/xml") in exported.headers[
        "content-type"
    ]

    org_id = (await auth_client.get("/api/v1/auth/me")).json()["user"]["org_id"]
    before = await _payout_snapshot(_db, org_id, bid, rid)
    assert before["batch"][0]["status"] == "open"
    assert before["batch"][0]["export_count"] == 1
    assert before["batch"][0]["exported_at"] is not None
    assert bool(before["batch"][0]["last_msg_id"]) == (export_route == "sepa")
    assert before["report"][0]["payout_batch_id"] == bid
    assert sum(row["action"] == audit.A.REIMBURSE_EXPORTED for row in before["audit"]) == 1
    assert not any(row["action"] == audit.A.REIMBURSE_CANCEL for row in before["audit"])
    assert len(before["deliveries"]) == len(before["jobs"]) == 1

    # Repeat the refusal: neither request releases a report or emits a cancel.
    for _ in range(2):
        cancelled = await auth_client.delete(f"/api/v1/reimbursements/{bid}")
        assert cancelled.status_code == 409, cancelled.text
        assert cancelled.json()["code"] == "batch_exported_cannot_cancel"
        assert cancelled.headers["X-Request-ID"]
        assert await _payout_snapshot(_db, org_id, bid, rid) == before
    readback = await auth_client.get(f"/api/v1/reimbursements/{bid}")
    assert readback.status_code == 200, readback.text
    assert readback.json()["report_count"] == 1
    assert readback.json()["reports"][0]["id"] == rid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_evidence",
    [
        {"export_count": 1},
        {"export_count": -1},
        {"exported_at": datetime(2026, 10, 1, tzinfo=UTC)},
        {"last_msg_id": "REIMB-SYNTHETIC-LEGACY"},
        {"last_msg_id": ""},
    ],
    ids=["positive-count", "negative-count", "timestamp-only", "message-only", "empty-message"],
)
async def test_legacy_export_evidence_prevents_cancel_without_writes(
    auth_client, client, _db, legacy_evidence
):
    """A contradictory legacy marker cannot make an issued instruction payable again."""
    await _activate(auth_client)
    emp = await _member(auth_client, client, "legacy-payee@corp.io")
    rid = await _approved(auth_client, client, emp, "Synthetic legacy export", "60.00")
    created = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert created.status_code == 201, created.text
    bid = created.json()["id"]
    org_id = (await auth_client.get("/api/v1/auth/me")).json()["user"]["org_id"]
    async with _db() as db:
        batch = await db.get(ReimbursementBatch, bid)
        for field, value in legacy_evidence.items():
            setattr(batch, field, value)
        await db.commit()
    before = await _payout_snapshot(_db, org_id, bid, rid)
    cancelled = await auth_client.delete(f"/api/v1/reimbursements/{bid}")
    assert cancelled.status_code == 409, cancelled.text
    assert cancelled.json()["code"] == "batch_exported_cannot_cancel"
    assert await _payout_snapshot(_db, org_id, bid, rid) == before


@pytest.mark.asyncio
async def test_pay_is_version_guarded(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e5@corp.io")
    rid = await _approved(auth_client, client, emp)
    b = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})).json()
    # The version check happens before the R6 maker≠checker check, so the
    # creator (owner) still exercises the stale-version 409 path directly.
    stale = await auth_client.post(
        f"/api/v1/reimbursements/{b['id']}/pay", json={"version": 999, "reference": "X"}
    )
    assert stale.status_code == 409
    # Board R6: the batch's creator (the owner) may not also be its payer — a
    # second approver pays it.
    payer = await _member(auth_client, client, "payer5@corp.io", role="admin")
    # A paid batch can't be paid again.
    ok = await client.post(
        f"/api/v1/reimbursements/{b['id']}/pay",
        headers=_h(payer),
        json={"version": b["version"]},
    )
    assert ok.status_code == 200
    twice = await client.post(
        f"/api/v1/reimbursements/{b['id']}/pay",
        headers=_h(payer),
        json={"version": ok.json()["version"]},
    )
    assert twice.status_code == 409


@pytest.mark.asyncio
async def test_export_csv(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e6@corp.io")
    rid = await _approved(auth_client, client, emp, "Berlin", "150.00")
    b = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})).json()
    exp = await auth_client.post(f"/api/v1/reimbursements/{b['id']}/export")
    assert exp.status_code == 200
    assert "text/csv" in exp.headers["content-type"]
    body = exp.text
    assert "Employee" in body and "Berlin" in body and "150.00" in body


@pytest.mark.asyncio
async def test_non_approver_cannot_run_payouts(auth_client, client, _db):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e7@corp.io")
    rid = await _approved(auth_client, client, emp)
    # The employee (role user, not an approver) cannot create a payout batch.
    r = await client.post("/api/v1/reimbursements", headers=_h(emp), json={"report_ids": [rid]})
    assert r.status_code == 403
    batch = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert batch.status_code == 201, batch.text
    bid = batch.json()["id"]
    assert (await auth_client.post(f"/api/v1/reimbursements/{bid}/export")).status_code == 200
    org_id = (await auth_client.get("/api/v1/auth/me")).json()["user"]["org_id"]
    before = await _payout_snapshot(_db, org_id, bid, rid)
    denied = await client.delete(f"/api/v1/reimbursements/{bid}", headers=_h(emp))
    assert denied.status_code == 403, denied.text
    assert denied.json() == r.json()  # preserve the existing permission refusal
    assert "export_count" not in denied.json()
    assert await _payout_snapshot(_db, org_id, bid, rid) == before


@pytest.mark.asyncio
async def test_cross_tenant_isolation(auth_client, client, _db):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e8@corp.io")
    rid = await _approved(auth_client, client, emp)
    b = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})).json()
    assert (await auth_client.post(f"/api/v1/reimbursements/{b['id']}/export")).status_code == 200
    org_id = (await auth_client.get("/api/v1/auth/me")).json()["user"]["org_id"]
    before = await _payout_snapshot(_db, org_id, b["id"], rid)

    # A second, unrelated org (owner is an approver; enable its module).
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other",
            "name": "Other",
            "email": "other@other.io",
            "password": "supersecret",
        },
    )
    other = reg.json()["token"]["access_token"]
    await client.put("/api/v1/modules/expenses", json={"enabled": True}, headers=_h(other))
    # Can't see our batch, and can't batch our report.
    assert (
        await client.get(f"/api/v1/reimbursements/{b['id']}", headers=_h(other))
    ).status_code == 404
    steal = await client.post(
        "/api/v1/reimbursements", headers=_h(other), json={"report_ids": [rid]}
    )
    assert steal.status_code == 422  # report not found in the other org
    denied = await client.delete(f"/api/v1/reimbursements/{b['id']}", headers=_h(other))
    assert denied.status_code == 404, denied.text
    assert "export_count" not in denied.json()
    assert await _payout_snapshot(_db, org_id, b["id"], rid) == before


@pytest.mark.asyncio
async def test_export_csv_is_formula_injection_safe(auth_client, client):
    """Board R1: `reimbursement.export_csv` writes `r.employee_name` and
    `r.title` — both free text (an employee's account name, a self-typed
    report title) — into a payroll-adjacent CSV an HR/finance user opens
    straight in Excel. Neither may reach the file un-neutralised (CWE-1236),
    and the numeric amount column must never be touched."""
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e9@corp.io", name="=cmd|'/c calc'!A1")
    rid = await _approved(auth_client, client, emp, "@SUM(A1:A9)", "150.00")
    b = (await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})).json()
    exp = await auth_client.post(f"/api/v1/reimbursements/{b['id']}/export")
    assert exp.status_code == 200
    rows = list(csv.reader(io.StringIO(exp.text)))
    header = rows[0]
    data = rows[1]
    assert header == [
        "batch_reference",
        "employee",
        "report",
        "amount",
        "currency",
        "amount_eur",
        "method",
    ]
    # Both attacker-influenceable free-text cells are neutralised with a
    # leading quote so Excel/Sheets renders them as inert text, not formulas.
    assert data[1].startswith("'="), data[1]
    assert data[2].startswith("'@"), data[2]
    # The numeric amount column is never sanitised (it must stay a real
    # number a spreadsheet can sum, not text).
    assert data[3] == "150.00"
