"""Expense-management build — the full sellable feature set.

Covers: multi-currency + rounding, mileage/per-diem computation, the report state
machine (submit/withdraw/approve/reject/return/mark-for-reimbursement/mark-
reimbursed) with illegal-transition guards, authorization/segregation of duties,
duplicate detection, and every configurable policy check (warn vs block).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _activate(auth_client):
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    ).status_code == 200


async def _member(auth_client, client, email, role="user"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Emp", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


def _item(**over):
    """A submittable item (business purpose + a missing-receipt declaration so no
    file upload is needed), overridable per test."""
    base = {
        "spend_date": "2026-05-01",
        "category": "travel",
        "description": "X",
        "amount": "100.00",
        "vat_amount": "0",
        "comment": "Client visit",
        "missing_receipt_declaration": "Receipt lost in transit",
    }
    base.update(over)
    return base


async def _create(cl, headers=None, items=None, currency="EUR", title="Trip"):
    r = await cl.post(
        "/api/v1/expenses",
        headers=headers or {},
        json={"title": title, "currency": currency, "items": items or [_item()]},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --------------------------------------------------------------------------- #
# Currencies + rounding + computed entry kinds
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fx_conversion_rounds_half_up(auth_client):
    """WO-8: one convention — a stated rate is foreign units per 1 EUR and the
    conversion DIVIDES (24.69 / 2 = 12.345 → 12.35, HALF_UP). The old assertion
    here encoded the multiply bug (100 × 1.23456) and a free-text fx_source;
    provenance is now server-derived from the closed enum."""
    await _activate(auth_client)
    rid = await _create(
        auth_client,
        items=[
            _item(
                amount="0",
                currency="USD",
                original_amount="24.69",
                fx_rate="2",  # 24.69 / 2 = 12.345 → 12.35 (ROUND_HALF_UP)
            )
        ],
    )
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    it = detail["items"][0]
    assert Decimal(it["amount"]) == Decimal("12.35")
    assert it["currency"] == "USD"
    assert it["fx_source"] == "stated"  # server-derived provenance, closed enum
    assert Decimal(detail["total"]) == Decimal("12.35")


@pytest.mark.asyncio
async def test_mileage_and_per_diem_amounts_are_computed(auth_client):
    await _activate(auth_client)
    rid = await _create(
        auth_client,
        items=[
            _item(
                amount="0",
                expense_type="mileage",
                mileage_distance="100",
                mileage_rate="0.305",  # → 30.50
                mileage_unit="km",
                description="Drive to site",
            ),
            _item(
                amount="0",
                expense_type="per_diem",
                per_diem_days="3",
                per_diem_rate="45.00",  # → 135.00
                description="Per diem",
            ),
        ],
    )
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    amounts = sorted(Decimal(i["amount"]) for i in detail["items"])
    assert amounts == [Decimal("30.50"), Decimal("135.00")]
    assert Decimal(detail["total"]) == Decimal("165.50")


@pytest.mark.asyncio
async def test_mileage_and_per_diem_submit_without_receipt(auth_client):
    """Computed allowances need no receipt to pass the compliance gate."""
    await _activate(auth_client)
    rid = await _create(
        auth_client,
        items=[
            {
                "spend_date": "2026-05-01",
                "category": "transport",
                "description": "Drive",
                "amount": "0",
                "expense_type": "mileage",
                "mileage_distance": "50",
                "mileage_rate": "0.30",
                "comment": "Site visit",
            }
        ],
    )
    r = await auth_client.post(f"/api/v1/expenses/{rid}/submit")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "submitted"


# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_full_lifecycle_submit_approve_reimburse(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp1@corp.io")
    rid = await _create(client, headers=_h(emp))
    assert (await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))).status_code == 200

    # Owner (approver by default) drives the rest. Read-then-write the version
    # (R4 optimistic concurrency) so this closure works across the sequence.
    async def dec(action):
        cur = await auth_client.get(f"/api/v1/expenses/{rid}")
        return await auth_client.post(
            f"/api/v1/expenses/{rid}/decision",
            json={"action": action, "version": cur.json()["version"]},
        )

    assert (await dec("approve")).json()["status"] == "approved"
    assert (await dec("mark_for_reimbursement")).json()["status"] == "marked_for_reimbursement"
    r = await dec("mark_reimbursed")
    assert r.json()["status"] == "reimbursed"
    assert r.json()["reimbursed_at"] is not None


@pytest.mark.asyncio
async def test_withdraw_returns_to_draft(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp2@corp.io")
    rid = await _create(client, headers=_h(emp))
    await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    r = await client.post(f"/api/v1/expenses/{rid}/withdraw", headers=_h(emp))
    assert r.status_code == 200
    assert r.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_return_for_correction_then_resubmit(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp3@corp.io")
    rid = await _create(client, headers=_h(emp))
    await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    ret = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "return_for_correction", "note": "add detail", "version": 1},
    )
    assert ret.json()["status"] == "returned"
    # A returned report is editable again by the owner, then resubmittable.
    patch = await client.patch(
        f"/api/v1/expenses/{rid}/items/{ret.json()['items'][0]['id']}",
        headers=_h(emp),
        json={"comment": "Client visit — expanded"},
    )
    assert patch.status_code == 200
    assert (await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))).json()[
        "status"
    ] == "submitted"


@pytest.mark.asyncio
async def test_illegal_transitions_conflict(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp-illegal@corp.io")
    # Approving a DRAFT (owned by the employee, so segregation-of-duties passes) → 409.
    emp_draft = await _create(client, headers=_h(emp))
    assert (
        await auth_client.post(
            f"/api/v1/expenses/{emp_draft}/decision", json={"action": "approve", "version": 1}
        )
    ).status_code == 409
    # Withdrawing a report that is not submitted → 409 (owner withdraws own draft).
    own_draft = await _create(auth_client)
    assert (await auth_client.post(f"/api/v1/expenses/{own_draft}/withdraw")).status_code == 409


# --------------------------------------------------------------------------- #
# Authorization / segregation of duties
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_non_approver_cannot_decide(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp4@corp.io")
    rid = await _create(client, headers=_h(emp))
    await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    r = await client.post(
        f"/api/v1/expenses/{rid}/decision",
        headers=_h(emp),
        json={"action": "approve", "version": 1},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_cannot_approve_own_report(auth_client):
    await _activate(auth_client)
    rid = await _create(auth_client)  # owner's own report
    await auth_client.post(f"/api/v1/expenses/{rid}/submit")
    r = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision", json={"action": "approve", "version": 1}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_cannot_withdraw_another_users_report(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp5@corp.io")
    rid = await _create(auth_client)  # owner's report
    await auth_client.post(f"/api/v1/expenses/{rid}/submit")
    r = await client.post(f"/api/v1/expenses/{rid}/withdraw", headers=_h(emp))
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Duplicate detection
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_duplicate_amount_date_merchant_flagged(auth_client):
    await _activate(auth_client)
    await auth_client.put("/api/v1/expenses/policy", json={"duplicate_detection": True})
    dup = _item(amount="20.00", merchant="Cafe Central", spend_date="2026-05-03")
    rid = await _create(auth_client, items=[dup, dict(dup)])
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    codes = {v["code"] for v in detail["policy_violations"]}
    assert "duplicate_amount_date_merchant" in codes


# --------------------------------------------------------------------------- #
# Configurable policy checks (warn) + blocking
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_out_of_policy_category_and_currency(auth_client):
    await _activate(auth_client)
    await auth_client.put(
        "/api/v1/expenses/policy",
        json={"allowed_categories": ["travel"], "allowed_currencies": ["USD"]},
    )
    rid = await _create(auth_client, currency="EUR", items=[_item(category="meals")])
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    codes = {v["code"] for v in detail["policy_violations"]}
    assert "out_of_policy_category" in codes
    assert "unsupported_currency" in codes  # report EUR not in [USD]


@pytest.mark.asyncio
async def test_weekend_and_mileage_rate_flags(auth_client):
    await _activate(auth_client)
    await auth_client.put(
        "/api/v1/expenses/policy",
        json={"warn_weekend": True, "mileage_rate": "0.30", "mileage_rate_tolerance": "0.02"},
    )
    saturday = "2022-01-01"  # a known Saturday
    rid = await _create(
        auth_client,
        items=[
            _item(
                spend_date=saturday,
                amount="0",
                expense_type="mileage",
                mileage_distance="100",
                mileage_rate="0.50",  # far above 0.30 ± 0.02
            )
        ],
    )
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    codes = {v["code"] for v in detail["policy_violations"]}
    assert {"weekend_spend", "mileage_rate"} <= codes


@pytest.mark.asyncio
async def test_late_submission_flag(auth_client):
    await _activate(auth_client)
    await auth_client.put("/api/v1/expenses/policy", json={"late_submission_days": 30})
    old = (date.today() - timedelta(days=90)).isoformat()
    rid = await _create(auth_client, items=[_item(spend_date=old)])
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    codes = {v["code"] for v in detail["policy_violations"]}
    assert "late_submission" in codes


@pytest.mark.asyncio
async def test_policy_flags_but_does_not_block_by_default(auth_client):
    """A flagged (non-blocking) expense still submits — flagged for review only."""
    await _activate(auth_client)
    await auth_client.put("/api/v1/expenses/policy", json={"max_item_amount": "50"})
    rid = await _create(auth_client, items=[_item(amount="500.00")])
    r = await auth_client.post(f"/api/v1/expenses/{rid}/submit")
    assert r.status_code == 200
    assert r.json()["status"] == "submitted"


@pytest.mark.asyncio
async def test_blocking_rule_prevents_submission(auth_client):
    await _activate(auth_client)
    await auth_client.put(
        "/api/v1/expenses/policy",
        json={"max_item_amount": "50", "blocking_rules": ["over_item_max"]},
    )
    rid = await _create(auth_client, items=[_item(amount="500.00")])
    blocked = await auth_client.post(f"/api/v1/expenses/{rid}/submit")
    assert blocked.status_code == 422
    assert "blocked by policy" in blocked.text.lower()

    # The dry-run endpoint reports the same block.
    check = (await auth_client.get(f"/api/v1/expenses/{rid}/policy-check")).json()
    assert check["blocking"] is True
    assert check["can_submit"] is False

    # Remove the block → it now submits (still flagged for review).
    await auth_client.put(
        "/api/v1/expenses/policy",
        json={"max_item_amount": "50", "blocking_rules": []},
    )
    assert (await auth_client.post(f"/api/v1/expenses/{rid}/submit")).status_code == 200


@pytest.mark.asyncio
async def test_marked_for_reimbursement_report_is_batchable(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "emp6@corp.io")
    rid = await _create(client, headers=_h(emp))
    await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    approved = await auth_client.post(
        f"/api/v1/expenses/{rid}/decision", json={"action": "approve", "version": 1}
    )
    await auth_client.post(
        f"/api/v1/expenses/{rid}/decision",
        json={"action": "mark_for_reimbursement", "version": approved.json()["version"]},
    )
    batch = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert batch.status_code == 201, batch.text
    # Board R6: the batch's creator (the owner) may not also be its payer — a
    # second approver pays it.
    payer = await _member(auth_client, client, "payer6@corp.io", role="admin")
    pay = await client.post(
        f"/api/v1/reimbursements/{batch.json()['id']}/pay",
        headers=_h(payer),
        json={"reference": "PAY-1", "version": batch.json()["version"]},
    )
    assert pay.status_code == 200, pay.text
    assert (await auth_client.get(f"/api/v1/expenses/{rid}")).json()["status"] == "reimbursed"
