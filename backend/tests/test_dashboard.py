"""The composed home dashboard (WO-16 / I1.1) — an Insight-layer PROJECTION.

Proves the ADR-0023 projection rule (every figure string-equals its canonical
endpoint — no forked math), the per-section permission/module gating (a section
the caller may not see is null, never zeroed, while the route itself serves
every business role), the SoD scoping of "waiting on me" (own submissions and
own payment runs never counted), tenant isolation with identical-looking data,
and the new `workflow_state` worklist filter (incl. its `in_approval` alias).
"""

import io

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

_CSV = (
    "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    "Fictional Fuels OU,INV-CAP-9,2026-06-01,Diesel,10,1.50,15.00,21\n"
)

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _member(auth_client, client, email, role="admin"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _me_id(client, token) -> str:
    return (await client.get("/api/v1/auth/me", headers=_h(token))).json()["user"]["id"]


async def _make_invoice(client, token, number, *, unit_price="100", due_date="2026-12-01"):
    r = await client.post(
        "/api/v1/invoices",
        headers=_h(token),
        json={
            "vendor_name": "Acme Supplies",
            "invoice_number": number,
            "issue_date": "2026-05-01",
            "due_date": due_date,
            "currency": "EUR",
            "line_items": [
                {"description": "W", "quantity": "1", "unit_price": unit_price, "tax_rate": "0"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _approve_and(auth_client, approver_token, iid, target=None):
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    assert sub.status_code == 200, sub.text
    appr = await auth_client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(approver_token),
        json={"version": sub.json()["version"]},
    )
    assert appr.status_code == 200, appr.text
    if target:
        t = await auth_client.post(
            f"/api/v1/invoices/{iid}/transition",
            json={"version": appr.json()["version"], "target": target},
        )
        assert t.status_code == 200, t.text


async def _dash(c, headers=None):
    r = await c.get("/api/v1/dashboard", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_dashboard_empty_org_owner_sees_all_sections_zeroed(auth_client):
    d = await _dash(auth_client)
    # Owner holds every permission → permission-gated sections all present…
    assert d["approvals"]["total"] == 0
    assert d["approvals"]["invoice_count"] == 0 and d["approvals"]["invoices"] == []
    assert d["approvals"]["payment_runs"] == 0 and d["approvals"]["vendor_changes"] == 0
    assert d["captures"] == {"pending": 0, "low_confidence_fields": 0}
    assert d["payables"]["overdue_count"] == 0 and d["payables"]["due_soon_count"] == 0
    assert d["cash"]["net_position"] == "0.00"
    assert d["cash"]["receivables_outstanding"] == "0.00"
    # …but module-gated pieces are null while their module is off (default):
    assert d["approvals"]["expense_reports"] is None  # expenses module off
    assert d["receivables"] is None  # issuing module off


@pytest.mark.asyncio
async def test_dashboard_projects_canonical_figures_exactly(auth_client, client, parse_upload):
    """Seed every surface, then assert each dashboard figure STRING-EQUALS the
    canonical endpoint's own figure — the no-forked-math proof (ADR-0023)."""
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")

    # AR: issuing on + one open sales invoice (1000 @ 21% = 1210).
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    r = await auth_client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "Globex",
            "issue_date": "2026-05-10",
            "due_date": "2026-12-01",
            "vat_scheme": "standard",
            "lines": [
                {"description": "S", "quantity": "1", "unit_price": "1000", "vat_rate": "21"}
            ],
        },
    )
    assert r.status_code == 201, r.text

    # AP: one overdue payable, one due far out, one awaiting approval.
    over = await _make_invoice(
        client, auth_client.headers["Authorization"].split()[1], "INV-OVER", due_date="2026-01-15"
    )
    await _approve_and(auth_client, approver, over)
    later = await _make_invoice(
        client, auth_client.headers["Authorization"].split()[1], "INV-LATER", due_date="2026-12-01"
    )
    await _approve_and(auth_client, approver, later, target="scheduled_for_payment")
    waiting = await _make_invoice(
        client, auth_client.headers["Authorization"].split()[1], "INV-WAIT"
    )
    sub = await auth_client.post(f"/api/v1/invoices/{waiting}/submit", json={"version": 1})
    assert sub.status_code == 200, sub.text

    # Captures: one parsed run pending review (CSV → defaulted headers flag).
    await parse_upload(auth_client, {"file": ("cap.csv", io.BytesIO(_CSV.encode()), "text/csv")})

    # Expenses: module on; an employee submits a report.
    await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    emp = await _member(auth_client, client, "emp@acme.io", role="user")
    rep = await client.post(
        "/api/v1/expenses",
        headers=_h(emp),
        json={
            "title": "Trip",
            "currency": "EUR",
            "items": [
                {
                    "spend_date": "2026-05-01",
                    "category": "travel",
                    "description": "Flight",
                    "amount": "300.00",
                    "vat_amount": "0",
                }
            ],
        },
    )
    assert rep.status_code == 201, rep.text
    rid = rep.json()["id"]
    # Compliance gate: every item needs a business purpose + receipt to submit.
    for it in (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp))).json()["items"]:
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
    sub_exp = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert sub_exp.status_code == 200, sub_exp.text

    # Payment run: the APPROVER creates it → it awaits the owner's check.
    run = await client.post(
        "/api/v1/payment-runs",
        headers=_h(approver),
        json={"invoice_ids": [later], "method": "bank_transfer"},
    )
    assert run.status_code == 201, run.text

    # Vendor change: approver requests an IBAN change on an established vendor.
    v = await auth_client.post(
        "/api/v1/vendors", json={"name": "Steel GmbH", "iban": "DE89370400440532013000"}
    )
    assert v.status_code == 201, v.text
    ch = await client.patch(
        f"/api/v1/vendors/{v.json()['id']}",
        headers=_h(approver),
        json={"iban": "NL91ABNA0417164300"},
    )
    assert ch.status_code == 200, ch.text

    d = await _dash(auth_client)

    # Payables ≡ /analytics/ap-aging (canonical).
    aging = (await auth_client.get("/api/v1/analytics/ap-aging")).json()
    assert d["payables"]["currency"] == aging["currency"]
    assert d["payables"]["overdue_count"] == aging["overdue_count"] == 1
    assert d["payables"]["overdue_amount"] == aging["overdue_amount"] == "100.00"
    assert d["payables"]["due_soon_count"] == aging["due_soon_count"]
    assert d["payables"]["due_soon_amount"] == aging["due_soon_amount"]
    assert d["payables"]["other_currencies"] == aging["other_currencies"]

    # Cash ≡ /analytics/cash-position (canonical).
    cash = (await auth_client.get("/api/v1/analytics/cash-position")).json()
    assert d["cash"]["net_position"] == cash["net_position"]
    assert d["cash"]["receivables_outstanding"] == cash["receivables"]["outstanding"] == "1210.00"
    assert d["cash"]["payables_outstanding"] == cash["payables"]["outstanding"]
    assert d["cash"]["currency"] == cash["currency"]

    # Receivables ≡ /issued/reports/receivables (canonical).
    rec = (await auth_client.get("/api/v1/issued/reports/receivables")).json()
    assert d["receivables"]["outstanding"] == rec["total_outstanding"] == "1210.00"
    assert d["receivables"]["overdue"] == rec["overdue_outstanding"]
    assert d["receivables"]["currency"] == rec["currency"]

    # Captures ≡ /invoices/captures/review (canonical queue).
    queue = (await auth_client.get("/api/v1/invoices/captures/review")).json()
    assert d["captures"]["pending"] == queue["total"] == 1
    assert d["captures"]["low_confidence_fields"] == sum(
        it["low_confidence_fields"] for it in queue["items"]
    )
    assert d["captures"]["low_confidence_fields"] > 0  # CSV defaults flag headers

    # Approvals ≡ the canonical counts. The OWNER submitted INV-WAIT, so SoD
    # keeps it out of the owner's own inbox; it waits on the APPROVER.
    summary = (await auth_client.get("/api/v1/expenses/summary")).json()
    assert d["approvals"]["expense_reports"] == summary["pending_approvals"] == 1
    assert d["approvals"]["invoice_count"] == 0  # own submission — SoD
    assert d["approvals"]["payment_runs"] == 1  # made by the approver, checked by me
    assert d["approvals"]["vendor_changes"] == 1  # requested by the approver
    assert d["approvals"]["total"] == 3

    da = await _dash(client, headers=_h(approver))
    assert [i["invoice_number"] for i in da["approvals"]["invoices"]] == ["INV-WAIT"]
    assert da["approvals"]["invoices"][0]["total"] == "100.00"
    assert da["approvals"]["invoice_count"] == 1
    # The approver made the run and requested the change → neither waits on THEM.
    assert da["approvals"]["payment_runs"] == 0
    assert da["approvals"]["vendor_changes"] == 0


@pytest.mark.asyncio
async def test_dashboard_approvals_waiting_on_me_scoping(auth_client, client):
    """A named step waits on its assignee only; a later pending step of a chain
    is not yet actionable; the submitter never sees their own invoice (SoD)."""
    owner_token = auth_client.headers["Authorization"].split()[1]
    a_tok = await _member(auth_client, client, "a@acme.io", role="admin")
    b_tok = await _member(auth_client, client, "b@acme.io", role="admin")
    a_id = await _me_id(client, a_tok)
    b_id = await _me_id(client, b_tok)

    # Two-step chain A → B.
    pol = await auth_client.post(
        "/api/v1/approval-policies",
        json={"name": "two-step", "priority": 10, "approver_ids": [a_id, b_id]},
    )
    assert pol.status_code == 201, pol.text
    iid = await _make_invoice(client, owner_token, "INV-CHAIN")
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    assert sub.status_code == 200, sub.text

    # Current step is A's: A sees it, B does not (B's step is later), the
    # submitting owner does not (SoD) even though the owner holds INVOICE_APPROVE.
    da = await _dash(client, headers=_h(a_tok))
    assert [i["invoice_id"] for i in da["approvals"]["invoices"]] == [iid]
    db_ = await _dash(client, headers=_h(b_tok))
    assert db_["approvals"]["invoice_count"] == 0
    downer = await _dash(auth_client)
    assert downer["approvals"]["invoice_count"] == 0

    # A approves → the chain advances → now it waits on B, not A.
    appr = await client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(a_tok),
        json={"version": sub.json()["version"]},
    )
    assert appr.status_code == 200, appr.text
    assert (await _dash(client, headers=_h(a_tok)))["approvals"]["invoice_count"] == 0
    db_ = await _dash(client, headers=_h(b_tok))
    assert [i["invoice_id"] for i in db_["approvals"]["invoices"]] == [iid]


@pytest.mark.asyncio
async def test_dashboard_sections_null_by_permission(auth_client, client):
    """The route serves EVERY business role (INVOICE_READ); sections narrow to
    each canonical surface's own permission — null, never zeroed, never 403."""
    # EMPLOYEE (legacy 'user'): no REPORT_READ / ISSUED_READ / approve perms.
    emp = await _member(auth_client, client, "emp@corp.io", role="user")
    d = await _dash(client, headers=_h(emp))
    assert d["captures"] is not None  # INVOICE_READ — every role
    assert d["payables"] is None and d["cash"] is None  # REPORT_READ
    assert d["receivables"] is None
    assert d["approvals"] is None  # holds no approve-side permission

    # READ_ONLY (legacy 'user_free'): read-everything, approve-nothing.
    ro = await _member(auth_client, client, "ro@corp.io", role="user_free")
    d = await _dash(client, headers=_h(ro))
    assert d["cash"] is not None and d["payables"] is not None
    assert d["approvals"] is None

    # Unauthenticated → 401 (the route is not public). `auth_client` IS `client`
    # with a default bearer header — drop it so this request is anonymous.
    del client.headers["Authorization"]
    assert (await client.get("/api/v1/dashboard")).status_code == 401


@pytest.mark.asyncio
async def test_dashboard_sections_null_when_module_disabled(auth_client):
    """Module gating mirrors the canonical routes: issuing off → receivables
    null; expenses off → the expense count null — even for the owner."""
    d = await _dash(auth_client)
    assert d["receivables"] is None
    assert d["approvals"]["expense_reports"] is None

    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    d = await _dash(auth_client)
    assert d["receivables"] is not None
    assert d["approvals"]["expense_reports"] == 0


@pytest.mark.asyncio
async def test_dashboard_cross_tenant_zero(auth_client, client):
    """Tenant B carries IDENTICAL-LOOKING data (same invoice number, same
    amount); each dashboard shows only its own rows (§4.1)."""
    owner_token = auth_client.headers["Authorization"].split()[1]
    a_iid = await _make_invoice(client, owner_token, "INV-TWIN")
    assert (
        await auth_client.post(f"/api/v1/invoices/{a_iid}/submit", json={"version": 1})
    ).status_code == 200

    # Org B, same-looking invoice, also submitted.
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Bcme",
            "name": "B Owner",
            "email": "owner@bcme.io",
            "password": "supersecret",
        },
    )
    b_tok = reg.json()["token"]["access_token"]
    b_iid = await _make_invoice(client, b_tok, "INV-TWIN")
    assert (
        await client.post(
            f"/api/v1/invoices/{b_iid}/submit", headers=_h(b_tok), json={"version": 1}
        )
    ).status_code == 200

    # B's approver sees exactly B's row — zero of A's — and vice versa. (The
    # submitting owners themselves see zero — SoD — so approvers assert this.)
    a_appr = await _member(auth_client, client, "appr@acme.io", role="admin")
    da = await _dash(client, headers=_h(a_appr))
    assert [i["invoice_id"] for i in da["approvals"]["invoices"]] == [a_iid]

    inv_b = await client.post(
        "/api/v1/team/invites",
        headers=_h(b_tok),
        json={"email": "appr@bcme.io", "role": "admin"},
    )
    acc_b = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv_b.json()["token"], "name": "M", "password": "supersecret"},
    )
    b_appr = acc_b.json()["token"]["access_token"]
    db_ = await _dash(client, headers=_h(b_appr))
    assert [i["invoice_id"] for i in db_["approvals"]["invoices"]] == [b_iid]
    assert a_iid != b_iid
    # B's other sections see none of A's data either.
    assert db_["captures"]["pending"] == 0
    assert db_["cash"]["payables_outstanding"] == "0.00"


@pytest.mark.asyncio
async def test_invoices_workflow_state_filter(auth_client, client):
    """`?workflow_state=` filters the worklist; `in_approval` covers both
    live-chain states; an unknown value is a 422; items carry the state."""
    owner_token = auth_client.headers["Authorization"].split()[1]
    drafted = await _make_invoice(client, owner_token, "INV-DRAFT")
    submitted = await _make_invoice(client, owner_token, "INV-SUB")
    assert (
        await auth_client.post(f"/api/v1/invoices/{submitted}/submit", json={"version": 1})
    ).status_code == 200

    r = await auth_client.get("/api/v1/invoices?workflow_state=submitted")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["id"] for i in items] == [submitted]
    assert items[0]["workflow_state"] == "submitted"

    r = await auth_client.get("/api/v1/invoices?workflow_state=in_approval")
    assert [i["id"] for i in r.json()["items"]] == [submitted]

    r = await auth_client.get("/api/v1/invoices?workflow_state=draft")
    assert drafted in [i["id"] for i in r.json()["items"]]

    assert (await auth_client.get("/api/v1/invoices?workflow_state=bogus")).status_code == 422


@pytest.mark.asyncio
async def test_expense_summary_and_capture_queue_unchanged(auth_client, parse_upload):
    """Behaviour-preserving proof for the two refactored consumers: the shapes
    and figures of /expenses/summary and /invoices/captures/review are what the
    pre-refactor code produced for the same fixtures."""
    await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    s = (await auth_client.get("/api/v1/expenses/summary")).json()
    assert {"my_draft", "my_submitted", "my_reimbursable", "pending_approvals"} <= set(s)
    assert s["pending_approvals"] == 0

    await parse_upload(auth_client, {"file": ("cap.csv", io.BytesIO(_CSV.encode()), "text/csv")})
    q = (await auth_client.get("/api/v1/invoices/captures/review")).json()
    assert q["total"] == 1 and len(q["items"]) == 1
    it = q["items"][0]
    assert {"extraction_run_id", "method", "low_confidence_fields", "duplicate_candidate"} <= set(
        it
    )
