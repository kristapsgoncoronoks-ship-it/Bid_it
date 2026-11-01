"""EX17: report decisions cannot fabricate reimbursement payment evidence.

These HTTP checks use synthetic source rows and compare committed state. Native
PostgreSQL locking/races and canonical keyed batch payment have separate suites.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.core.tenant import reset_current_org, set_current_org
from app.models.audit import AuditEvent
from app.models.expense import EXPENSE_STATUSES, ExpenseCommand, ExpenseReport, ReimbursementBatch
from app.models.expense_approval import ExpenseApprovalStep
from app.models.job import Job
from app.models.membership import Membership
from app.models.user import User, UserRole
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.services import expense_state

ALIASES = ("mark_reimbursed", "reimburse")
STAMP = datetime(2026, 10, 25, 10, 30, tzinfo=UTC)


async def _activate(client):
    response = await client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert response.status_code == 200, response.text


async def _seed(db, *, status="approved", linked=False):
    owner = await db.scalar(select(User).where(User.email == "owner@acme.io"))
    claimant = User(
        org_id=owner.org_id,
        email=f"ex17-claimant-{uuid4().hex[:10]}@example.com",
        name="Synthetic EX17 claimant",
        role=UserRole.user,
        hashed_password="fixture-no-login",
    )
    db.add(claimant)
    await db.flush()
    batch = None
    if linked:
        batch = ReimbursementBatch(
            org_id=owner.org_id,
            status="open",
            method="bank_transfer",
            total_eur=Decimal("123.45"),
            created_by=owner.email,
            created_by_id=owner.id,
        )
        db.add(batch)
        await db.flush()
    report = ExpenseReport(
        org_id=owner.org_id,
        employee_id=claimant.id,
        employee_name=claimant.name,
        title="Synthetic EX17 confidential claim",
        status=status,
        currency="EUR",
        total=Decimal("123.45"),
        vat_total=Decimal("0.00"),
        total_eur=Decimal("123.45"),
        version=7,
        payout_batch_id=batch.id if batch else None,
        reimbursed_at=STAMP if status == "reimbursed" else None,
    )
    db.add(report)
    db.add(
        WebhookEndpoint(
            org_id=owner.org_id,
            url="https://example.test/ex17-hook",
            secret="synthetic-secret",
            events="expense.reimbursed",
        )
    )
    await db.flush()
    db.add(
        ExpenseApprovalStep(
            org_id=owner.org_id,
            report_id=report.id,
            seq=0,
            status="approved" if status in ("approved", "marked_for_reimbursement") else "pending",
        )
    )
    await db.commit()
    return owner, report


async def _state(factory, org_id):
    token = set_current_org(org_id)
    try:
        async with factory() as db:
            return {
                model.__tablename__: [
                    dict(row)
                    for row in (
                        await db.execute(
                            select(model.__table__).where(model.org_id == org_id).order_by(model.id)
                        )
                    ).mappings()
                ]
                for model in (
                    ExpenseReport,
                    ExpenseApprovalStep,
                    ReimbursementBatch,
                    ExpenseCommand,
                    AuditEvent,
                    WebhookDelivery,
                    Job,
                )
            }
    finally:
        reset_current_org(token)


@pytest.mark.parametrize("action", ALIASES)
@pytest.mark.parametrize("source", EXPENSE_STATUSES)
def test_report_state_machine_never_offers_a_direct_payment_transition(action, source):
    assert not expense_state.can(action, source)
    with pytest.raises(ConflictError) as caught:
        expense_state.target_for(action, source)
    assert caught.value.code == "reimbursement_batch_required"
    assert caught.value.status == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ALIASES)
@pytest.mark.parametrize("source", EXPENSE_STATUSES)
async def test_direct_reimbursement_aliases_refuse_without_any_committed_effect(
    auth_client, db_session, _db, action, source
):
    await _activate(auth_client)
    owner, report = await _seed(db_session, status=source)
    before = await _state(_db, owner.org_id)
    response = await auth_client.post(
        f"/api/v1/expenses/{report.id}/decision",
        json={"action": action, "version": report.version, "note": "Synthetic direct attempt"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "reimbursement_batch_required"
    assert "Reimbursements" in response.json()["detail"]
    assert await _state(_db, owner.org_id) == before
    # Historical terminal facts remain readable, with no invented batch/receipt.
    readback = await auth_client.get(f"/api/v1/expenses/{report.id}")
    assert readback.status_code == 200, readback.text
    assert readback.json()["status"] == source
    assert before["expense_reports"][0]["payout_batch_id"] is None
    if source == "reimbursed":
        assert datetime.fromisoformat(readback.json()["reimbursed_at"]) == STAMP.replace(
            tzinfo=None
        )
        assert readback.json()["payment_reference"] is None
    assert await _state(_db, owner.org_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ALIASES)
@pytest.mark.parametrize("source", ("approved", "marked_for_reimbursement"))
async def test_linked_report_cannot_skip_its_maker_checker_batch_payment(
    auth_client, db_session, _db, action, source
):
    await _activate(auth_client)
    owner, report = await _seed(db_session, status=source, linked=True)
    before = await _state(_db, owner.org_id)
    response = await auth_client.post(
        f"/api/v1/expenses/{report.id}/decision",
        json={"action": action, "version": report.version},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "reimbursement_batch_required"
    assert await _state(_db, owner.org_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ALIASES)
@pytest.mark.parametrize(
    "boundary", ("employee", "self", "foreign", "module", "offboarded", "stale")
)
async def test_terminal_refusal_preserves_existing_access_and_version_guards(
    auth_client, db_session, _db, role_client, action, boundary
):
    await _activate(auth_client)
    owner, report = await _seed(db_session)
    caller, expected, version = auth_client, 403, report.version
    membership = await db_session.scalar(
        select(Membership).where(Membership.org_id == owner.org_id, Membership.user_id == owner.id)
    )
    if boundary == "employee":
        owner.role = membership.role = UserRole.user
        owner.is_expense_approver = membership.is_expense_approver = False
    elif boundary == "self":
        report.employee_id, report.employee_name = owner.id, owner.name
    elif boundary == "foreign":
        caller = await role_client("owner")
        await _activate(caller)
        expected = 404
    elif boundary == "module":
        assert (
            await auth_client.put("/api/v1/modules/expenses", json={"enabled": False})
        ).status_code == 200
    elif boundary == "offboarded":
        membership.status, expected = "suspended", 401
    else:
        version, expected = report.version - 1, 409
    await db_session.commit()
    before = await _state(_db, owner.org_id)
    response = await caller.post(
        f"/api/v1/expenses/{report.id}/decision", json={"action": action, "version": version}
    )
    assert response.status_code == expected, response.text
    assert response.json().get("code") != "reimbursement_batch_required"
    assert report.title not in response.text
    assert await _state(_db, owner.org_id) == before
