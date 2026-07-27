"""Board R6 — segregation of duties on reimbursement payout batches (§4.8): the
batch's creator (maker) can never mark it paid (checker) — enforced for EVERY
role including the org Owner. The only exemption is the cross-tenant platform
operator, and it must be EXPLICIT (`override_sod=true`) and AUDITED
(`reimbursement.sod_override` naming the overridden control). Mirrors
`tests/test_payment_runs_sod.py` for the analogous AP payment-run control."""

import json

import pytest
from sqlalchemy import select, update

from app.models.audit import AuditEvent
from app.models.user import User

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


async def _member(auth_client, client, email, role="user", name="M"):
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


async def _open_batch(auth_client, client, email="emp@corp.io"):
    """A batch CREATED by the owner (auth_client), one approved report inside,
    still OPEN (unpaid)."""
    emp = await _member(auth_client, client, email)
    rid = await _approved(auth_client, client, emp)
    b = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert b.status_code == 201, b.text
    return b.json()


@pytest.mark.asyncio
async def test_creator_cannot_mark_batch_paid(auth_client, client):
    await _activate(auth_client)
    b = await _open_batch(auth_client, client)

    denied = await auth_client.post(
        f"/api/v1/reimbursements/{b['id']}/pay",
        json={"version": b["version"], "reference": "PAY-1"},
    )
    assert denied.status_code == 403, denied.text
    body = denied.json()
    assert body["code"] == "maker_is_checker"
    assert "creator" in body["detail"]

    # The batch is genuinely unchanged — still open, not paid.
    still = await auth_client.get(f"/api/v1/reimbursements/{b['id']}")
    assert still.json()["status"] == "open"


@pytest.mark.asyncio
async def test_different_user_can_pay(auth_client, client):
    await _activate(auth_client)
    payer = await _member(auth_client, client, "payer@corp.io", role="admin")
    b = await _open_batch(auth_client, client, email="e2@corp.io")

    paid = await client.post(
        f"/api/v1/reimbursements/{b['id']}/pay",
        headers=_h(payer),
        json={"version": b["version"], "reference": "PAY-2"},
    )
    assert paid.status_code == 200, paid.text
    body = paid.json()
    assert body["status"] == "paid"
    assert body["created_by"] == "owner@acme.io"


@pytest.mark.asyncio
async def test_platform_admin_override_is_audited(auth_client, client, db_session):
    await _activate(auth_client)
    b = await _open_batch(auth_client, client, email="e3@corp.io")

    # Make the owner (the batch's CREATOR) a platform operator.
    await db_session.execute(
        update(User).where(User.email == "owner@acme.io").values(is_platform_admin=True)
    )
    await db_session.commit()

    # Even a platform admin is refused WITHOUT the explicit flag — never silent.
    implicit = await auth_client.post(
        f"/api/v1/reimbursements/{b['id']}/pay",
        json={"version": b["version"], "reference": "X"},
    )
    assert implicit.status_code == 403 and implicit.json()["code"] == "maker_is_checker"

    # With the explicit flag the pay succeeds AND the override event names the control.
    paid = await auth_client.post(
        f"/api/v1/reimbursements/{b['id']}/pay",
        json={"version": b["version"], "reference": "X", "override_sod": True},
    )
    assert paid.status_code == 200, paid.text
    ev = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "reimbursement.sod_override")
        .order_by(AuditEvent.seq.desc())
    )
    assert ev is not None and ev.target_id == b["id"]
    meta = json.loads(ev.meta)
    assert meta["control"] == "maker_is_checker"
    assert meta["stage"] == "pay" and meta["conflict_role"] == "creator"

    # A NON-platform user never gets the override, flag or not.
    await db_session.execute(
        update(User).where(User.email == "owner@acme.io").values(is_platform_admin=False)
    )
    await db_session.commit()
    b2 = await _open_batch(auth_client, client, email="e4@corp.io")
    still = await auth_client.post(
        f"/api/v1/reimbursements/{b2['id']}/pay",
        json={"version": b2["version"], "override_sod": True},
    )
    assert still.status_code == 403 and still.json()["code"] == "maker_is_checker"
