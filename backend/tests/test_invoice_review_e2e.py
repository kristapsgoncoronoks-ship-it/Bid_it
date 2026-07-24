"""End-to-end tests for the supplier-invoice review & approval workflow (Phase 08).

Covers: side-by-side review payload + reconciliation, editable header/lines with
tax validation, optimistic concurrency (stale version → 409), the full submit →
approve → schedule → pay path with record-locking, reject, return-for-correction,
reassign, controlled correction after approval, policy-driven sequential
approvers, segregation of duties, authorization (non-approver blocked),
comments + internal attachments, approval-policy CRUD, and cross-tenant isolation.
"""

import io

import pytest


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _member(auth_client, client, email, role="admin", name="Member"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    assert inv.status_code in (200, 201), inv.text
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": name, "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    return acc.json()["token"]["access_token"]


async def _me_id(client, token) -> str:
    r = await client.get("/api/v1/auth/me", headers=_h(token))
    return r.json()["user"]["id"]


async def _make_invoice(client, token, number="INV-100", unit_price="100", tax_rate="0"):
    r = await client.post(
        "/api/v1/invoices",
        headers=_h(token),
        json={
            "vendor_name": "Acme Supplies",
            "invoice_number": number,
            "issue_date": "2026-05-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Widgets",
                    "quantity": "1",
                    "unit_price": unit_price,
                    "tax_rate": tax_rate,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _review(client, token, iid):
    return (await client.get(f"/api/v1/invoices/{iid}/review", headers=_h(token))).json()


# --------------------------------------------------------------------------- #
# Review payload + editing + concurrency
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_review_payload_and_reconciliation(auth_client):
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    r = await auth_client.get(f"/api/v1/invoices/{iid}/review")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workflow_state"] == "draft"
    assert body["workflow_label"] == "Draft"
    assert body["editable"] is True and body["locked"] is False
    assert body["reconciliation"]["balanced"] is True
    assert "submitted" in body["allowed_targets"]  # draft → submitted is legal
    assert body["version"] == 1


@pytest.mark.asyncio
async def test_optimistic_concurrency_on_edit(auth_client):
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    # Stale version → 409.
    stale = await auth_client.patch(
        f"/api/v1/invoices/{iid}/review", json={"version": 99, "notes": "x"}
    )
    assert stale.status_code == 409
    # Correct version → ok, version bumps.
    ok = await auth_client.patch(
        f"/api/v1/invoices/{iid}/review", json={"version": 1, "notes": "reviewed"}
    )
    assert ok.status_code == 200 and ok.json()["version"] == 2
    assert ok.json()["notes"] == "reviewed"


@pytest.mark.asyncio
async def test_edit_lines_recomputes_and_validates_tax(auth_client):
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    # Bad tax rate → 422.
    bad = await auth_client.put(
        f"/api/v1/invoices/{iid}/lines",
        json={
            "version": 1,
            "lines": [{"description": "X", "quantity": "1", "unit_price": "10", "tax_rate": "150"}],
        },
    )
    assert bad.status_code == 422
    # Good edit recomputes totals (2 × 50 @ 20% = 100 + 20 tax).
    good = await auth_client.put(
        f"/api/v1/invoices/{iid}/lines",
        json={
            "version": 1,
            "lines": [{"description": "X", "quantity": "2", "unit_price": "50", "tax_rate": "20"}],
        },
    )
    assert good.status_code == 200, good.text
    body = good.json()
    assert body["subtotal"] == "100.00" and body["tax_amount"] == "20.00"
    assert body["total"] == "120.00"
    assert body["reconciliation"]["balanced"] is True


# --------------------------------------------------------------------------- #
# Full approval lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_submit_approve_schedule_pay_with_lock(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])

    # Submit (owner).
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    assert sub.status_code == 200, sub.text
    assert sub.json()["workflow_state"] == "submitted"
    ver = sub.json()["version"]

    # The approver approves the open step → approved + locked.
    appr = await client.post(
        f"/api/v1/invoices/{iid}/approve", headers=_h(approver), json={"version": ver}
    )
    assert appr.status_code == 200, appr.text
    body = appr.json()
    assert body["workflow_state"] == "approved" and body["locked"] is True

    # A locked invoice refuses edits.
    edit = await auth_client.patch(
        f"/api/v1/invoices/{iid}/review", json={"version": body["version"], "notes": "no"}
    )
    assert edit.status_code == 409

    # Schedule for payment, then mark paid (syncs the legacy status).
    sch = await auth_client.post(
        f"/api/v1/invoices/{iid}/transition",
        json={"version": body["version"], "target": "scheduled_for_payment"},
    )
    assert sch.status_code == 200 and sch.json()["workflow_state"] == "scheduled_for_payment"
    paid = await auth_client.post(
        f"/api/v1/invoices/{iid}/transition",
        json={"version": sch.json()["version"], "target": "paid"},
    )
    assert paid.status_code == 200 and paid.json()["workflow_state"] == "paid"
    assert paid.json()["status"] == "paid"  # aging status synced


@pytest.mark.asyncio
async def test_reject_flow(auth_client, client):
    approver = await _member(auth_client, client, "rej@acme.io", role="admin")
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    rej = await client.post(
        f"/api/v1/invoices/{iid}/reject",
        headers=_h(approver),
        json={"version": sub.json()["version"], "note": "wrong vendor"},
    )
    assert rej.status_code == 200 and rej.json()["workflow_state"] == "rejected"


@pytest.mark.asyncio
async def test_return_for_correction_then_resubmit(auth_client, client):
    approver = await _member(auth_client, client, "ret@acme.io", role="admin")
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    ret = await client.post(
        f"/api/v1/invoices/{iid}/return",
        headers=_h(approver),
        json={"version": sub.json()["version"], "note": "fix the total"},
    )
    assert ret.status_code == 200 and ret.json()["workflow_state"] == "draft"
    assert ret.json()["editable"] is True
    # Resubmit works (fresh chain).
    re = await auth_client.post(
        f"/api/v1/invoices/{iid}/submit", json={"version": ret.json()["version"]}
    )
    assert re.status_code == 200 and re.json()["workflow_state"] == "submitted"


@pytest.mark.asyncio
async def test_controlled_correction_after_approval(auth_client, client):
    approver = await _member(auth_client, client, "corr@acme.io", role="admin")
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    appr = await client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(approver),
        json={"version": sub.json()["version"]},
    )
    assert appr.json()["workflow_state"] == "approved"
    # Reopen the locked, approved invoice for correction → draft, unlocked, chain cleared.
    reopen = await auth_client.post(
        f"/api/v1/invoices/{iid}/transition",
        json={"version": appr.json()["version"], "target": "draft"},
    )
    assert reopen.status_code == 200, reopen.text
    body = reopen.json()
    assert body["workflow_state"] == "draft" and body["locked"] is False
    assert body["approval_steps"] == []  # approvals invalidated
    # Now editable again.
    edit = await auth_client.patch(
        f"/api/v1/invoices/{iid}/review", json={"version": body["version"], "notes": "corrected"}
    )
    assert edit.status_code == 200


# --------------------------------------------------------------------------- #
# Authorization + segregation of duties
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_submitter_cannot_approve_own(auth_client):
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    # Owner has INVOICE_APPROVE but submitted it → SoD 403.
    appr = await auth_client.post(
        f"/api/v1/invoices/{iid}/approve", json={"version": sub.json()["version"]}
    )
    assert appr.status_code == 403


@pytest.mark.asyncio
async def test_non_approver_cannot_approve(auth_client, client):
    employee = await _member(auth_client, client, "emp@acme.io", role="user")
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    appr = await client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(employee),
        json={"version": sub.json()["version"]},
    )
    assert appr.status_code == 403  # lacks INVOICE_APPROVE


# --------------------------------------------------------------------------- #
# Policy-driven sequential approval + reassign
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_policy_sequential_two_approvers(auth_client, client):
    a_tok = await _member(auth_client, client, "a@acme.io", role="admin")
    b_tok = await _member(auth_client, client, "b@acme.io", role="admin")
    a_id = await _me_id(client, a_tok)
    b_id = await _me_id(client, b_tok)
    # A policy routing all invoices through A then B.
    pol = await auth_client.post(
        "/api/v1/approval-policies",
        json={"name": "two-step", "priority": 10, "approver_ids": [a_id, b_id]},
    )
    assert pol.status_code == 201, pol.text

    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    assert sub.json()["workflow_state"] == "submitted"
    assert len(sub.json()["approval_steps"]) == 2

    # B cannot jump the queue (A's step is first).
    early = await client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(b_tok),
        json={"version": sub.json()["version"]},
    )
    assert early.status_code == 403

    # A approves → partially_approved.
    r1 = await client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(a_tok),
        json={"version": sub.json()["version"]},
    )
    assert r1.status_code == 200 and r1.json()["workflow_state"] == "partially_approved"

    # B approves → approved.
    r2 = await client.post(
        f"/api/v1/invoices/{iid}/approve", headers=_h(b_tok), json={"version": r1.json()["version"]}
    )
    assert r2.status_code == 200 and r2.json()["workflow_state"] == "approved"


@pytest.mark.asyncio
async def test_reassign_pending_step(auth_client, client):
    a_tok = await _member(auth_client, client, "ra@acme.io", role="admin")
    b_tok = await _member(auth_client, client, "rb@acme.io", role="admin")
    a_id = await _me_id(client, a_tok)
    b_id = await _me_id(client, b_tok)
    pol = await auth_client.post(
        "/api/v1/approval-policies", json={"name": "single", "approver_ids": [a_id]}
    )
    assert pol.status_code == 201
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    # Reassign A's step to B.
    re = await auth_client.post(
        f"/api/v1/invoices/{iid}/reassign",
        json={"version": sub.json()["version"], "approver_id": b_id},
    )
    assert re.status_code == 200, re.text
    # A can no longer approve; B can.
    ra = await client.post(
        f"/api/v1/invoices/{iid}/approve", headers=_h(a_tok), json={"version": re.json()["version"]}
    )
    assert ra.status_code == 403
    rb = await client.post(
        f"/api/v1/invoices/{iid}/approve", headers=_h(b_tok), json={"version": re.json()["version"]}
    )
    assert rb.status_code == 200 and rb.json()["workflow_state"] == "approved"


# --------------------------------------------------------------------------- #
# Comments + attachments
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_comments_and_attachments(auth_client):
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    c = await auth_client.post(f"/api/v1/invoices/{iid}/comments", json={"body": "please review"})
    assert c.status_code == 201 and c.json()["body"] == "please review"
    lst = await auth_client.get(f"/api/v1/invoices/{iid}/comments")
    assert len(lst.json()) == 1

    up = await auth_client.post(
        f"/api/v1/invoices/{iid}/attachments",
        files={"file": ("po.txt", io.BytesIO(b"purchase order 42"), "text/plain")},
        params={"note": "the PO"},
    )
    assert up.status_code == 201, up.text
    aid = up.json()["id"]
    got = await auth_client.get(f"/api/v1/invoices/{iid}/attachments/{aid}/download")
    assert got.status_code == 200 and got.content == b"purchase order 42"


# --------------------------------------------------------------------------- #
# Policy CRUD + cross-tenant isolation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_policy_crud_with_concurrency(auth_client):
    c = await auth_client.post(
        "/api/v1/approval-policies", json={"name": "p1", "min_amount": "500"}
    )
    assert c.status_code == 201
    pid, ver = c.json()["id"], c.json()["version"]
    # Stale update → 409.
    stale = await auth_client.patch(
        f"/api/v1/approval-policies/{pid}", json={"version": 999, "name": "x"}
    )
    assert stale.status_code == 409
    ok = await auth_client.patch(
        f"/api/v1/approval-policies/{pid}", json={"version": ver, "priority": 5}
    )
    assert ok.status_code == 200 and ok.json()["priority"] == 5 and ok.json()["version"] == ver + 1
    dele = await auth_client.delete(f"/api/v1/approval-policies/{pid}")
    assert dele.status_code == 204
    assert (await auth_client.get("/api/v1/approval-policies")).json() == []


@pytest.mark.asyncio
async def test_cross_tenant_cannot_access_review(auth_client, client):
    iid = await _make_invoice(auth_client, auth_client.headers["Authorization"].split()[1])
    # A second, unrelated org.
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
    r = await client.get(f"/api/v1/invoices/{iid}/review", headers=_h(other))
    assert r.status_code == 404
    a = await client.post(f"/api/v1/invoices/{iid}/approve", headers=_h(other), json={"version": 1})
    assert a.status_code == 404
