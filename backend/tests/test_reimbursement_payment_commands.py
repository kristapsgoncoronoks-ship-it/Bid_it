"""EX20 exact operator payment records and immutable recovery through HTTP.

Approval states are seeded; no provider, bank, SMTP or worker delivery runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.expense import ExpenseCommand, ExpenseReport, ReimbursementBatch
from app.models.job import Job
from app.models.membership import Membership
from app.models.user import User, UserRole
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.services import audit, jobs

BASE = "/api/v1/reimbursements"


async def _activate(client):
    result = await client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert result.status_code == 200, result.text


async def _seed(db, *, maker_is_owner=False, hook=True):
    owner = await db.scalar(select(User).where(User.email == "owner@acme.io"))
    people = [
        User(
            org_id=owner.org_id,
            email=f"ex20-{kind}-{uuid4().hex[:10]}@example.com",
            name=f"Synthetic {kind}",
            role=UserRole.user,
            hashed_password="fixture-no-login",
        )
        for kind in ("maker", "claimant")
    ]
    db.add_all(people)
    await db.flush()
    maker, claimant = people
    batch = ReimbursementBatch(
        org_id=owner.org_id,
        status="open",
        method="bank_transfer",
        total_eur=Decimal("0.30"),
        version=3,
        created_by=owner.email if maker_is_owner else maker.email,
        created_by_id=owner.id if maker_is_owner else maker.id,
    )
    db.add(batch)
    await db.flush()
    reports = [
        ExpenseReport(
            org_id=owner.org_id,
            employee_id=claimant.id,
            employee_name=claimant.name,
            title=f"Synthetic payment claim {i}",
            status="approved" if i == 0 else "marked_for_reimbursement",
            currency="EUR",
            total=Decimal(amount),
            total_eur=None,
            vat_total=Decimal("0.00"),
            version=7 + i,
            payout_batch_id=batch.id,
        )
        for i, amount in enumerate(("0.10", "0.20"))
    ]
    db.add_all(reports)
    if hook:
        db.add(
            WebhookEndpoint(
                org_id=owner.org_id,
                url="https://example.test/ex20-hook",
                secret="synthetic-secret",
                events="expense.reimbursed",
            )
        )
    await db.commit()
    return owner, batch, reports


def _body(batch, reports):
    return {
        "version": batch.version,
        "reference": None,
        "method": None,
        "override_sod": False,
        "expected_total_eur": str(batch.total_eur),
        "report_versions": {r.id: r.version for r in reports},
    }


def _key(value=None):
    return {"Idempotency-Key": value or str(uuid4())}


async def _state(factory, org_id):
    async with factory() as session:
        return {
            m.__tablename__: [
                dict(r)
                for r in (
                    await session.execute(
                        select(m.__table__).where(m.org_id == org_id).order_by(m.id)
                    )
                ).mappings()
            ]
            for m in (
                ReimbursementBatch,
                ExpenseReport,
                ExpenseCommand,
                AuditEvent,
                WebhookDelivery,
                Job,
            )
        }


@pytest.mark.asyncio
async def test_payment_receipt_preserves_exact_record_and_replays_without_effects(
    auth_client, db_session, _db
):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session)
    body, key = _body(batch, reports), str(uuid4())
    response = await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key(key))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "paid" and result["total_eur"] == "0.30"
    assert result["reference"] is None and result["method"] == "bank_transfer"
    assert result["version"] == 4 and result["report_count"] == 2
    assert {r["id"]: r["version"] for r in result["reports"]} == {
        r.id: r.version + 1 for r in reports
    }
    evidence = result["payment_evidence"]
    assert evidence["basis"] == "operator_recorded" and evidence["bank_confirmation"] == "unknown"
    assert evidence["recorded_by_id"] == owner.id and evidence["recorded_at"]
    assert all(
        r["status"] == "reimbursed" and r["payment_reference"] is None for r in result["reports"]
    )
    committed = await _state(_db, owner.org_id)
    assert len(committed["expense_commands"]) == 1
    receipt = committed["expense_commands"][0]
    assert receipt["operation"] == "reimbursement_paid" and receipt["employee_id"] == owner.id
    assert receipt["command_key"] == key and json.loads(receipt["result_json"]) == result
    assert len(committed["webhook_deliveries"]) == len(committed["jobs"]) == 1
    assert committed["webhook_deliveries"][0]["idempotency_key"]
    assert json.loads(committed["webhook_deliveries"][0]["payload_json"]) == {
        "event": "expense.reimbursed",
        "data": {"id": batch.id, "reports": 2, "reference": None, "total_eur": "0.30"},
    }
    assert sum(r["action"] == audit.A.REIMBURSE_PAID for r in committed["audit_events"]) == 1
    replay = await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key(key))
    assert replay.status_code == 200 and replay.json() == result, replay.text
    lookup = await auth_client.get(f"{BASE}/commands/{key}")
    assert lookup.status_code == 200 and lookup.json()["operation"] == "reimbursement_paid"
    assert lookup.json()["result"] == result
    detail = await auth_client.get(f"{BASE}/{batch.id}")
    assert detail.status_code == 200 and detail.json()["payment_evidence"] == evidence
    assert await _state(_db, owner.org_id) == committed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["reference", "method", "version", "total", "report_version", "batch"]
)
async def test_payment_key_refuses_changed_inputs_without_writes(
    auth_client, db_session, _db, changed
):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session)
    body, key = _body(batch, reports), str(uuid4())
    assert (
        await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key(key))
    ).status_code == 200
    before = await _state(_db, owner.org_id)
    altered, target = {**body, "report_versions": dict(body["report_versions"])}, batch.id
    if changed == "reference":
        altered["reference"] = "Different record"
    elif changed == "method":
        altered["method"] = "cash"
    elif changed == "version":
        altered["version"] += 1
    elif changed == "total":
        altered["expected_total_eur"] = "0.31"
    elif changed == "report_version":
        altered["report_versions"][reports[0].id] += 1
    else:
        target = str(uuid4())
    response = await auth_client.post(f"{BASE}/{target}/pay", json=altered, headers=_key(key))
    assert (
        response.status_code == 409
        and response.json()["code"] == "expense_command_payload_conflict"
    ), response.text
    assert await _state(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_payment_key_cannot_be_reused_by_another_expense_operation(
    auth_client, db_session, _db
):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session)
    key = str(uuid4())
    assert (
        await auth_client.post(
            f"{BASE}/{batch.id}/pay", json=_body(batch, reports), headers=_key(key)
        )
    ).status_code == 200
    before = await _state(_db, owner.org_id)
    response = await auth_client.post(
        "/api/v1/expenses",
        json={"title": "Other financial intention", "currency": "EUR", "items": []},
        headers=_key(key),
    )
    assert (
        response.status_code == 409
        and response.json()["code"] == "expense_command_payload_conflict"
    ), response.text
    assert await _state(_db, owner.org_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "no_total",
        "no_versions",
        "zero_report_version",
        "extra_version",
        "reference_too_long",
        "method_too_long",
        "bad_total",
        "provider_flag",
    ],
)
async def test_payment_inputs_are_validated_before_mutation(auth_client, db_session, _db, bad):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session)
    body = _body(batch, reports)
    if bad == "no_total":
        body.pop("expected_total_eur")
    elif bad == "no_versions":
        body.pop("report_versions")
    elif bad == "zero_report_version":
        body["report_versions"][reports[0].id] = 0
    elif bad == "extra_version":
        body["report_versions"][str(uuid4())] = 2
    elif bad == "reference_too_long":
        body["reference"] = "R" * 201
    elif bad == "method_too_long":
        body["method"] = "M" * 41
    elif bad == "bad_total":
        body["expected_total_eur"] = "not-money"
    else:
        body["provider_confirmed"] = True
    before = await _state(_db, owner.org_id)
    response = await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key())
    assert response.status_code == (409 if bad == "extra_version" else 422), response.text
    assert await _state(_db, owner.org_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        "batch_version",
        "report_version",
        "reviewed_total",
        "stored_total",
        "report_total",
        "terminal_report",
        "payment_fields",
        "missing_report",
        "foreign_fx",
    ],
)
async def test_payment_refuses_changed_or_contradictory_state(
    auth_client, db_session, _db, changed
):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session)
    body, code = _body(batch, reports), "reimbursement_selection_conflict"
    if changed == "batch_version":
        body["version"] -= 1
        code = "reimbursement_payment_conflict"
    elif changed == "report_version":
        body["report_versions"][reports[0].id] -= 1
    elif changed == "reviewed_total":
        body["expected_total_eur"] = "0.31"
        code = "reimbursement_total_conflict"
    elif changed == "stored_total":
        batch.total_eur = Decimal("0.31")
        code = "reimbursement_total_conflict"
    elif changed == "report_total":
        reports[0].total = Decimal("0.11")
        code = "reimbursement_total_conflict"
    elif changed == "terminal_report":
        reports[0].status = "reimbursed"
    elif changed == "payment_fields":
        reports[0].payment_reference = "Earlier record"
    elif changed == "missing_report":
        reports[0].payout_batch_id = None
    else:
        reports[0].currency = "USD"
        reports[0].total_eur = None
        code = "reimbursement_total_conflict"
    await db_session.commit()
    before = await _state(_db, owner.org_id)
    response = await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key())
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code
    assert await _state(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_legacy_no_key_payment_records_basis_but_has_no_implicit_replay(
    auth_client, db_session, _db
):
    await _activate(auth_client)
    owner, batch, _ = await _seed(db_session)
    first = await auth_client.post(f"{BASE}/{batch.id}/pay", json={"version": 3, "reference": None})
    assert first.status_code == 200, first.text
    assert (
        first.json()["reference"] is None
        and first.json()["payment_evidence"]["basis"] == "operator_recorded"
    )
    before = await _state(_db, owner.org_id)
    second = await auth_client.post(
        f"{BASE}/{batch.id}/pay", json={"version": 3, "reference": None}
    )
    assert second.status_code == 409
    assert await _state(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_legacy_paid_batch_does_not_invent_operator_or_bank_evidence(auth_client, db_session):
    await _activate(auth_client)
    _, batch, _ = await _seed(db_session, hook=False)
    batch.status, batch.paid_at, batch.reference = "paid", datetime.now(UTC), None
    await db_session.commit()
    response = await auth_client.get(f"{BASE}/{batch.id}")
    assert response.status_code == 200, response.text
    assert response.json()["payment_evidence"] == {
        "basis": "legacy_unknown",
        "bank_confirmation": "unknown",
        "recorded_by_id": None,
        "recorded_at": None,
    }
    assert "command_key" not in response.text


@pytest.mark.asyncio
async def test_maker_and_override_control_remain_explicit_and_audited(auth_client, db_session, _db):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session, maker_is_owner=True)
    body = _body(batch, reports)
    before = await _state(_db, owner.org_id)
    refused = await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key())
    assert refused.status_code == 403 and refused.json()["code"] == "maker_is_checker"
    assert await _state(_db, owner.org_id) == before
    owner.is_platform_admin = True
    await db_session.commit()
    body["override_sod"] = True
    response = await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key())
    assert response.status_code == 200, response.text
    state = await _state(_db, owner.org_id)
    assert sum(r["action"] == audit.A.REIMBURSE_SOD_OVERRIDE for r in state["audit_events"]) == 1
    assert sum(r["action"] == audit.A.REIMBURSE_PAID for r in state["audit_events"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "override_audit", "enqueue", "receipt"])
async def test_payment_effect_failure_rolls_back_entire_record(
    auth_client, db_session, _db, monkeypatch, failure
):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session, maker_is_owner=failure == "override_audit")
    body = _body(batch, reports)
    if failure == "override_audit":
        owner.is_platform_admin, body["override_sod"] = True, True
        await db_session.commit()
    before = await _state(_db, owner.org_id)
    if failure in ("audit", "override_audit"):
        original = audit.record
        refused_action = (
            audit.A.REIMBURSE_PAID if failure == "audit" else audit.A.REIMBURSE_SOD_OVERRIDE
        )

        async def no_audit(*args, **kwargs):
            if len(args) > 1 and args[1] == refused_action:
                return None
            return await original(*args, **kwargs)

        monkeypatch.setattr(audit, "record", no_audit)
    elif failure == "enqueue":

        async def no_job(*args, **kwargs):
            raise AppError("Synthetic queue refusal", code="synthetic_queue_refusal", status=503)

        monkeypatch.setattr(jobs, "enqueue", no_job)
    else:
        from sqlalchemy.exc import OperationalError

        engine = db_session.bind.sync_engine

        def no_receipt(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("INSERT INTO EXPENSE_COMMANDS"):
                raise OperationalError(
                    statement, {}, RuntimeError("synthetic payment receipt refusal")
                )

        event.listen(engine, "before_cursor_execute", no_receipt)
    try:
        if failure == "receipt":
            with pytest.raises(Exception, match="synthetic payment receipt refusal"):
                await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key())
        else:
            response = await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key())
            assert response.status_code == 503, response.text
    finally:
        if failure == "receipt":
            event.remove(engine, "before_cursor_execute", no_receipt)
    assert await _state(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_demoted_checker_cannot_read_payment_receipt_through_any_alias(
    auth_client, db_session, _db
):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session)
    key = str(uuid4())
    response = await auth_client.post(
        f"{BASE}/{batch.id}/pay", json=_body(batch, reports), headers=_key(key)
    )
    assert response.status_code == 200, response.text
    member = await db_session.scalar(
        select(Membership).where(Membership.org_id == owner.org_id, Membership.user_id == owner.id)
    )
    member.role = owner.role = UserRole.user
    member.is_expense_approver = owner.is_expense_approver = False
    await db_session.commit()
    before = await _state(_db, owner.org_id)
    for path in (
        f"{BASE}/commands/{key}",
        f"/api/v1/expenses/commands/{key}",
        f"/api/v1/expenses/commands/{key}/result",
    ):
        denied = await auth_client.get(path)
        assert denied.status_code == 403, denied.text
        assert reports[0].title not in denied.text
    assert await _state(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_payment_lookup_is_actor_scoped(auth_client, client, db_session):
    await _activate(auth_client)
    _, batch, reports = await _seed(db_session)
    key = str(uuid4())
    assert (
        await auth_client.post(
            f"{BASE}/{batch.id}/pay", json=_body(batch, reports), headers=_key(key)
        )
    ).status_code == 200
    assert (await auth_client.get(f"{BASE}/commands/{uuid4()}")).status_code == 404
    invitation = await auth_client.post(
        "/api/v1/team/invites", json={"email": "ex20-other-approver@example.com", "role": "admin"}
    )
    assert invitation.status_code == 201
    accepted = await client.post(
        "/api/v1/auth/accept-invite",
        json={
            "token": invitation.json()["token"],
            "name": "Other approver",
            "password": "supersecret",
        },
    )
    assert accepted.status_code == 201
    other = await client.get(
        f"{BASE}/commands/{key}",
        headers={"Authorization": f"Bearer {accepted.json()['token']['access_token']}"},
    )
    assert other.status_code == 404, other.text


@pytest.mark.asyncio
async def test_payment_projection_is_bounded_and_avoids_frozen_result_json(auth_client, db_session):
    await _activate(auth_client)
    owner, batch, _ = await _seed(db_session, hook=False)
    batch.status = "paid"
    batches = [batch] + [
        ReimbursementBatch(
            org_id=owner.org_id,
            status="paid",
            method="bank_transfer",
            total_eur=Decimal("0.30"),
            created_by=owner.email,
            created_by_id=owner.id,
        )
        for _ in range(99)
    ]
    db_session.add_all(batches[1:])
    await db_session.flush()
    # Invalid/large JSON ensures pages select only durable metadata columns.
    recording_time = datetime.now(UTC)
    for item in batches:
        item.paid_at = recording_time
        db_session.add(
            ExpenseCommand(
                org_id=owner.org_id,
                employee_id=owner.id,
                command_key=str(uuid4()),
                operation="reimbursement_paid",
                payload_hash="0" * 64,
                target_id=item.id,
                created_at=recording_time,
                result_json="unparsed-fixture:" + "x" * 10000,
            )
        )
    await db_session.commit()
    counts, engine = [], db_session.bind.sync_engine
    for size in (1, 20, 100):
        selects = []

        def track(_conn, _cursor, statement, _parameters, _context, _many, collected=selects):
            sql = statement.lstrip().lower()
            if sql.startswith("select") and any(
                name in sql
                for name in ("reimbursement_batches", "expense_reports", "expense_commands")
            ):
                collected.append(sql)

        event.listen(engine, "before_cursor_execute", track)
        try:
            response = await auth_client.get(f"{BASE}/page?page_size={size}")
        finally:
            event.remove(engine, "before_cursor_execute", track)
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == size and response.json()["total"] == 100
        assert all(
            r["payment_evidence"]["basis"] == "operator_recorded" for r in response.json()["items"]
        )
        assert all("command_key" not in r["payment_evidence"] for r in response.json()["items"])
        assert not any("result_json" in sql for sql in selects)
        counts.append(len(selects))
    assert counts[0] == counts[1] == counts[2] and 1 <= counts[0] <= 3
    batch.paid_at = None
    await db_session.commit()
    conflicting_time = await auth_client.get(f"{BASE}/{batch.id}")
    assert conflicting_time.status_code == 200
    assert conflicting_time.json()["payment_evidence"]["basis"] == "unavailable"
    batch.paid_at = recording_time
    db_session.add(
        ExpenseCommand(
            org_id=owner.org_id,
            employee_id=owner.id,
            command_key=str(uuid4()),
            operation="reimbursement_paid",
            payload_hash="1" * 64,
            target_id=batch.id,
            result_json="{}",
        )
    )
    await db_session.commit()
    duplicated = await auth_client.get(f"{BASE}/{batch.id}")
    assert duplicated.status_code == 200
    assert duplicated.json()["payment_evidence"] == {
        "basis": "unavailable",
        "bank_confirmation": "unknown",
        "recorded_by_id": None,
        "recorded_at": None,
    }


@pytest.mark.asyncio
async def test_payment_receipt_module_and_offboarding_guards(auth_client, db_session, _db):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session)
    key = str(uuid4())
    body = _body(batch, reports)
    assert (
        await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key(key))
    ).status_code == 200
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": False})
    ).status_code == 200
    before = await _state(_db, owner.org_id)
    assert (await auth_client.get(f"{BASE}/commands/{key}")).status_code == 403
    assert (
        await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key(key))
    ).status_code == 403
    assert await _state(_db, owner.org_id) == before
    await _activate(auth_client)
    member = await db_session.scalar(
        select(Membership).where(Membership.org_id == owner.org_id, Membership.user_id == owner.id)
    )
    member.status = "suspended"
    await db_session.commit()
    assert (await auth_client.get(f"{BASE}/commands/{key}")).status_code == 401


@pytest.mark.asyncio
async def test_payment_receipt_and_batch_are_tenant_scoped(
    auth_client, db_session, role_client, _db
):
    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session)
    key = str(uuid4())
    body = _body(batch, reports)
    assert (
        await auth_client.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key(key))
    ).status_code == 200
    before = await _state(_db, owner.org_id)
    other = await role_client("owner")
    await _activate(other)
    assert (await other.get(f"{BASE}/commands/{key}")).status_code == 404
    assert (await other.get(f"{BASE}/{batch.id}")).status_code == 404
    response = await other.post(f"{BASE}/{batch.id}/pay", json=body, headers=_key(key))
    assert response.status_code == 404, response.text
    assert reports[0].title not in response.text
    # Factory reads in the test explicitly select the original organization.
    from app.core.tenant import reset_current_org, set_current_org

    token = set_current_org(owner.org_id)
    try:
        assert await _state(_db, owner.org_id) == before
    finally:
        reset_current_org(token)


@pytest.mark.asyncio
async def test_historical_create_receipt_does_not_invent_report_versions(auth_client, db_session):
    from app.schemas.reimbursement import BatchCreate
    from app.services.issued_commands import fingerprint

    await _activate(auth_client)
    owner, batch, reports = await _seed(db_session, maker_is_owner=True, hook=False)
    current = await auth_client.get(f"{BASE}/{batch.id}")
    assert current.status_code == 200
    old_result = current.json()
    old_result.pop("payment_evidence")
    for row in old_result["reports"]:
        row.pop("version")
    body = {
        "report_ids": [r.id for r in reports],
        "report_versions": {r.id: r.version for r in reports},
        "method": "bank_transfer",
        "note": None,
    }
    canonical = BatchCreate.model_validate(body).model_dump(mode="json")
    canonical["report_ids"] = sorted(canonical["report_ids"])
    key = str(uuid4())
    db_session.add(
        ExpenseCommand(
            org_id=owner.org_id,
            employee_id=owner.id,
            command_key=key,
            operation="reimbursement_created",
            payload_hash=fingerprint(canonical),
            target_id=batch.id,
            result_json=json.dumps(old_result),
        )
    )
    await db_session.commit()
    replay = await auth_client.post(BASE, json=body, headers=_key(key))
    assert replay.status_code == 201, replay.text
    assert all(r["version"] is None for r in replay.json()["reports"])
    readback = await auth_client.get(f"{BASE}/{batch.id}")
    assert {r["id"]: r["version"] for r in readback.json()["reports"]} == {
        r.id: r.version for r in reports
    }
