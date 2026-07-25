"""WO-9 — segregation of duties on payment runs (§4.8): the maker (creator) can
never approve their own run, and neither the creator nor the approver can mark it
paid — enforced for EVERY role including the org Owner. The only exemption is the
cross-tenant platform operator, and it must be EXPLICIT (`override_sod=true`) and
AUDITED (`payment_run.sod_override` naming the overridden control)."""

import json

import pytest
from sqlalchemy import select, update

from app.models.audit import AuditEvent
from app.models.user import User


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _member(auth_client, client, email, role="admin"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _make_invoice(auth_client, number, unit_price="100"):
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Acme Supplies",
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


async def _open_run(auth_client, client, approver, number="INV-SOD"):
    """A run CREATED by the owner (auth_client) holding one scheduled invoice."""
    iid = await _make_invoice(auth_client, number)
    await _schedule(auth_client, approver, iid)
    run = await auth_client.post("/api/v1/payment-runs", json={"invoice_ids": [iid]})
    assert run.status_code == 201, run.text
    return run.json()


@pytest.mark.asyncio
async def test_creator_cannot_mark_run_paid(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io")
    run = await _open_run(auth_client, client, approver)

    # The creator cannot even APPROVE their own run…
    self_appr = await auth_client.post(
        f"/api/v1/payment-runs/{run['id']}/approve", json={"version": run["version"]}
    )
    assert self_appr.status_code == 403, self_appr.text
    assert self_appr.json()["code"] == "maker_is_checker"

    # …and after a second user approves it, the creator still cannot PAY it.
    appr = await client.post(
        f"/api/v1/payment-runs/{run['id']}/approve",
        headers=_h(approver),
        json={"version": run["version"]},
    )
    assert appr.status_code == 200, appr.text
    denied = await auth_client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        json={"version": appr.json()["version"], "reference": "X"},
    )
    assert denied.status_code == 403, denied.text
    body = denied.json()
    assert body["code"] == "maker_is_checker"
    assert "creator" in body["detail"]


@pytest.mark.asyncio
async def test_approver_cannot_be_the_payer(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io")
    run = await _open_run(auth_client, client, approver)
    appr = await client.post(
        f"/api/v1/payment-runs/{run['id']}/approve",
        headers=_h(approver),
        json={"version": run["version"]},
    )
    assert appr.status_code == 200, appr.text
    denied = await client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        headers=_h(approver),
        json={"version": appr.json()["version"], "reference": "X"},
    )
    assert denied.status_code == 403, denied.text
    body = denied.json()
    assert body["code"] == "maker_is_checker"
    assert "approver" in body["detail"]


@pytest.mark.asyncio
async def test_different_user_with_payment_write_can_pay(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io")
    payer = await _member(auth_client, client, "payer@acme.io")
    run = await _open_run(auth_client, client, approver)
    appr = await client.post(
        f"/api/v1/payment-runs/{run['id']}/approve",
        headers=_h(approver),
        json={"version": run["version"]},
    )
    paid = await client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        headers=_h(payer),
        json={"version": appr.json()["version"], "reference": "SEPA-1"},
    )
    assert paid.status_code == 200, paid.text
    body = paid.json()
    assert body["status"] == "paid"
    assert body["created_by"] == "owner@acme.io"
    assert body["approved_by"] == "appr@acme.io"


@pytest.mark.asyncio
async def test_platform_admin_override_is_audited(auth_client, client, db_session):
    approver = await _member(auth_client, client, "appr@acme.io")
    run = await _open_run(auth_client, client, approver)
    appr = await client.post(
        f"/api/v1/payment-runs/{run['id']}/approve",
        headers=_h(approver),
        json={"version": run["version"]},
    )
    assert appr.status_code == 200

    # Make the owner (the run's CREATOR) a platform operator.
    await db_session.execute(
        update(User).where(User.email == "owner@acme.io").values(is_platform_admin=True)
    )
    await db_session.commit()

    # Even a platform admin is refused WITHOUT the explicit flag — never silent.
    implicit = await auth_client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        json={"version": appr.json()["version"], "reference": "X"},
    )
    assert implicit.status_code == 403 and implicit.json()["code"] == "maker_is_checker"

    # With the explicit flag the pay succeeds AND the override event names the control.
    paid = await auth_client.post(
        f"/api/v1/payment-runs/{run['id']}/pay",
        json={"version": appr.json()["version"], "reference": "X", "override_sod": True},
    )
    assert paid.status_code == 200, paid.text
    ev = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "payment_run.sod_override")
        .order_by(AuditEvent.seq.desc())
    )
    assert ev is not None and ev.target_id == run["id"]
    meta = json.loads(ev.meta)
    assert meta["control"] == "maker_is_checker"
    assert meta["stage"] == "pay" and meta["conflict_role"] == "creator"

    # A NON-platform user never gets the override, flag or not.
    run2 = await _open_run(auth_client, client, approver, number="INV-SOD2")
    denied = await client.post(
        f"/api/v1/payment-runs/{run2['id']}/approve",
        headers=_h(approver),
        json={"version": run2["version"]},
    )
    assert denied.status_code == 200  # approver is fine
    still = await client.post(
        f"/api/v1/payment-runs/{run2['id']}/pay",
        headers=_h(approver),
        json={"version": denied.json()["version"], "override_sod": True},
    )
    assert still.status_code == 403 and still.json()["code"] == "maker_is_checker"
