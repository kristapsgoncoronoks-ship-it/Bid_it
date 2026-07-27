"""SQLite-level proof of the R4 optimistic-concurrency guard on
`POST /expenses/{id}/decision`: a stale `version` is refused 409 and the
report's decided state is left byte-identical to what the winning decision
already produced — the loser's write is not partially applied.

This is the cheap, single-process complement to
`tests/test_expense_decision_concurrency.py`'s genuine-concurrency Postgres-only
proof: SQLite can't reproduce a real two-writer race (one shared connection),
but it CAN prove the version check itself rejects a stale write, on every CI
job (not just the Postgres gate)."""

from __future__ import annotations

import pytest

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


async def _submitted_report(auth_client, client, emp) -> str:
    r = await client.post(
        "/api/v1/expenses",
        headers=_h(emp),
        json={
            "title": "Trip",
            "items": [
                {
                    "spend_date": "2026-05-01",
                    "category": "meals",
                    "description": "Dinner",
                    "amount": "42.00",
                    "vat_amount": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp))).json()
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Client visit"},
            headers=_h(emp),
        )
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("r.png", _PNG, "image/png")},
            headers=_h(emp),
        )
    submitted = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert submitted.status_code == 200, submitted.text
    return rid


@pytest.mark.asyncio
async def test_missing_version_is_422(auth_client, client):
    """A required field — matches BatchPay/ExpenseApprovalPolicyUpdate's existing
    precedent in this module family; a body without `version` never reaches the
    route's business logic at all."""
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp-noversion@corp.io")
    rid = await _submitted_report(auth_client, client, emp)
    r = await auth_client.post(f"/api/v1/expenses/{rid}/decision", json={"action": "approve"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_stale_version_returns_409_and_state_unchanged(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp-stale@corp.io")
    rid = await _submitted_report(auth_client, client, emp)

    # The owner (approver by default) decides once, successfully — version 1 -> 2.
    ok = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision", json={"action": "approve", "version": 1}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"
    won_version = ok.json()["version"]
    won_decided_at = ok.json()["decided_at"]
    assert won_version == 2

    # A second decision replaying the ORIGINAL (now stale) version is refused —
    # never a silent double-approve, never a partial write.
    stale = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "reject", "version": 1},  # stale: the row is already at 2
    )
    assert stale.status_code == 409
    assert "1" in stale.json()["detail"] and "2" in stale.json()["detail"]

    # The report is byte-identical to the winning decision's result — the
    # rejected (stale) call changed NOTHING.
    after = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    assert after["status"] == "approved"
    assert after["version"] == won_version
    # Compare the parsed instants — the GET path serializes the trailing "Z" the
    # POST response's inline JSON encoding doesn't, an unrelated formatting detail.
    assert after["decided_at"].rstrip("Z") == won_decided_at.rstrip("Z")

    # Replaying the CORRECT (current) version succeeds.
    ok2 = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "mark_for_reimbursement", "version": won_version},
    )
    assert ok2.status_code == 200, ok2.text
    assert ok2.json()["status"] == "marked_for_reimbursement"
    assert ok2.json()["version"] == won_version + 1
