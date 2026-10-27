"""EX18 reimbursement creation contracts through HTTP and committed rows.

Approved-state rows are synthetic fixtures: this suite qualifies batching and
recovery, not the preceding expense submission/approval journey. PostgreSQL
overlap proof lives in the CI-registered reimbursement concurrency module.
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.expense import ExpenseCommand, ExpenseReport, ReimbursementBatch
from app.models.job import Job
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.services import audit, jobs

BASE = "/api/v1/reimbursements"


async def _activate(client):
    response = await client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert response.status_code == 200, response.text


async def _seed(db, count=2, *, hook=True):
    owner = await db.scalar(select(User).where(User.email == "owner@acme.io"))
    claimant = User(
        org_id=owner.org_id,
        email=f"ex18-claimant-{uuid4().hex[:10]}@example.com",
        name="Synthetic claimant",
        hashed_password="synthetic-unused",
        role=UserRole.user,
    )
    db.add(claimant)
    await db.flush()
    rows = [
        ExpenseReport(
            org_id=owner.org_id,
            employee_id=claimant.id,
            employee_name=claimant.name,
            title=f"Synthetic approved {index:04d}",
            status="approved" if index % 2 == 0 else "marked_for_reimbursement",
            currency="EUR",
            total=Decimal("0.10") if index % 2 == 0 else Decimal("0.20"),
            total_eur=None,
            vat_total=Decimal("0.00"),
            version=3 + index,
        )
        for index in range(count)
    ]
    db.add_all(rows)
    if hook:
        db.add(
            WebhookEndpoint(
                org_id=owner.org_id,
                url="https://example.test/ex18-synthetic-hook",
                secret="synthetic-test-secret",
                events="expense.reimbursement_created",
            )
        )
    await db.commit()
    return owner, rows


def _body(rows):
    return {
        "report_ids": [row.id for row in rows],
        "report_versions": {row.id: row.version for row in rows},
        "method": "bank_transfer",
        "note": "Synthetic payout selection",
    }


def _key(value=None):
    return {"Idempotency-Key": value or str(uuid4())}


async def _snapshot(factory, org_id):
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
                ReimbursementBatch,
                ExpenseReport,
                ExpenseCommand,
                AuditEvent,
                WebhookDelivery,
                Job,
            )
        }


@pytest.mark.asyncio
async def test_create_replay_and_lookup_preserve_exact_committed_selection(
    auth_client, db_session, _db
):
    await _activate(auth_client)
    owner, rows = await _seed(db_session)
    body = _body(rows)
    key = str(uuid4())
    first = await auth_client.post(BASE, json=body, headers=_key(key))
    assert first.status_code == 201, first.text
    result = first.json()
    assert result["total_eur"] == "0.30"
    assert result["report_count"] == 2
    assert {row["id"] for row in result["reports"]} == set(body["report_ids"])
    assert {row["status"] for row in result["reports"]} == {"approved", "marked_for_reimbursement"}
    committed = await _snapshot(_db, owner.org_id)
    assert len(committed["reimbursement_batches"]) == 1
    assert len(committed["expense_commands"]) == 1
    assert [
        row["version"] for row in sorted(committed["expense_reports"], key=lambda row: row["title"])
    ] == [4, 5]
    assert {row["payout_batch_id"] for row in committed["expense_reports"]} == {result["id"]}
    actions = [row for row in committed["audit_events"] if row["action"] == audit.A.REIMBURSE_BATCH]
    assert len(actions) == 1
    assert json.loads(actions[0]["meta"])["reports"] == 2
    assert len(committed["webhook_deliveries"]) == len(committed["jobs"]) == 1
    delivery = committed["webhook_deliveries"][0]
    assert delivery["idempotency_key"]
    assert json.loads(delivery["payload_json"])["data"]["reports"] == 2

    # Opposite order is the same unique observed selection, not a new command.
    replay_body = {**body, "report_ids": list(reversed(body["report_ids"]))}
    replay = await auth_client.post(BASE, json=replay_body, headers=_key(key))
    assert replay.status_code == 201, replay.text
    assert replay.json() == result
    status = await auth_client.get(f"{BASE}/commands/{key}")
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "committed"
    assert status.json()["operation"] == "reimbursement_created"
    assert status.json()["target_id"] == result["id"]
    assert status.json()["result"] == result
    assert await _snapshot(_db, owner.org_id) == committed

    # A receipt describes the command's original outcome, not mutable live state.
    assert (await auth_client.delete(f"{BASE}/{result['id']}")).status_code == 204
    cancelled = await _snapshot(_db, owner.org_id)
    recovered = await auth_client.get(f"{BASE}/commands/{key}")
    assert recovered.json()["result"] == result
    replay = await auth_client.post(BASE, json=body, headers=_key(key))
    assert replay.status_code == 201 and replay.json() == result
    assert await _snapshot(_db, owner.org_id) == cancelled


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["note", "method", "version", "selection"])
async def test_existing_command_key_refuses_changed_inputs_without_writes(
    auth_client, db_session, _db, changed
):
    await _activate(auth_client)
    owner, rows = await _seed(db_session, 3)
    body = _body(rows[:2])
    key = str(uuid4())
    assert (await auth_client.post(BASE, json=body, headers=_key(key))).status_code == 201
    before = await _snapshot(_db, owner.org_id)
    changed_body = {**body, "report_versions": dict(body["report_versions"])}
    if changed == "note":
        changed_body["note"] = "Other intention"
    elif changed == "method":
        changed_body["method"] = "payroll"
    elif changed == "version":
        changed_body["report_versions"][rows[0].id] += 1
    else:
        changed_body = _body(rows[1:])
    result = await auth_client.post(BASE, json=changed_body, headers=_key(key))
    assert result.status_code == 409, result.text
    assert result.json()["code"] == "expense_command_payload_conflict"
    assert await _snapshot(_db, owner.org_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "empty",
        "duplicate",
        "invalid_id",
        "oversized",
        "no_versions",
        "missing_version",
        "extra_version",
        "zero_version",
    ],
)
async def test_invalid_observed_selection_is_refused_before_allocation(
    auth_client, db_session, _db, bad
):
    await _activate(auth_client)
    owner, rows = await _seed(db_session)
    body = _body(rows)
    if bad == "empty":
        body["report_ids"], body["report_versions"] = [], {}
    elif bad == "duplicate":
        body["report_ids"] = [rows[0].id, rows[0].id]
        body["report_versions"] = {rows[0].id: rows[0].version}
    elif bad == "invalid_id":
        body["report_ids"] = ["invalid-identifier"]
        body["report_versions"] = {"invalid-identifier": 1}
    elif bad == "oversized":
        body["report_ids"] = [str(uuid4()) for _ in range(1001)]
        body["report_versions"] = dict.fromkeys(body["report_ids"], 1)
    elif bad == "no_versions":
        body.pop("report_versions")
    elif bad == "missing_version":
        body["report_versions"].pop(rows[0].id)
    elif bad == "extra_version":
        body["report_versions"][str(uuid4())] = 1
    else:
        body["report_versions"][rows[0].id] = 0
    before = await _snapshot(_db, owner.org_id)
    response = await auth_client.post(BASE, json=body, headers=_key())
    assert response.status_code == 422, response.text
    assert await _snapshot(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_stale_report_version_is_a_no_write_conflict(auth_client, db_session, _db):
    await _activate(auth_client)
    owner, rows = await _seed(db_session)
    body = _body(rows)
    rows[0].version += 1
    await db_session.commit()
    before = await _snapshot(_db, owner.org_id)
    response = await auth_client.post(BASE, json=body, headers=_key())
    assert response.status_code == 409, response.text
    assert await _snapshot(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_aggregate_numeric_overflow_refuses_before_allocation(auth_client, db_session, _db):
    await _activate(auth_client)
    owner, rows = await _seed(db_session)
    rows[0].total_eur = Decimal("999999999999.99")
    await db_session.commit()
    before = await _snapshot(_db, owner.org_id)
    response = await auth_client.post(BASE, json=_body(rows), headers=_key())
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "reimbursement_total_out_of_range"
    assert await _snapshot(_db, owner.org_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["draft", "already_batched", "missing_fx", "foreign_id"])
async def test_ineligible_selection_refuses_whole_batch(auth_client, db_session, _db, state):
    await _activate(auth_client)
    owner, rows = await _seed(db_session)
    body = _body(rows)
    if state == "draft":
        rows[1].status = "draft"
    elif state == "already_batched":
        rows[1].payout_batch_id = str(uuid4())
    elif state == "missing_fx":
        rows[1].currency = "USD"
        rows[1].total_eur = None
    else:
        body["report_ids"][1] = str(uuid4())
        body["report_versions"] = dict(zip(body["report_ids"], [3, 4], strict=True))
    await db_session.commit()
    before = await _snapshot(_db, owner.org_id)
    response = await auth_client.post(BASE, json=body, headers=_key())
    assert response.status_code in (404, 409, 422), response.text
    assert await _snapshot(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_legacy_no_key_can_rebatch_after_cancel_but_never_replays_implicitly(
    auth_client, db_session
):
    await _activate(auth_client)
    _, rows = await _seed(db_session, 1)
    body = {"report_ids": [rows[0].id]}
    first = await auth_client.post(BASE, json=body)
    assert first.status_code == 201, first.text
    repeat = await auth_client.post(BASE, json=body)
    assert repeat.status_code == 422, repeat.text
    assert (await auth_client.delete(f"{BASE}/{first.json()['id']}")).status_code == 204
    next_batch = await auth_client.post(BASE, json=body)
    assert next_batch.status_code == 201, next_batch.text
    assert next_batch.json()["id"] != first.json()["id"]


@pytest.mark.asyncio
async def test_command_lookup_is_actor_scoped_and_cross_action_keys_conflict(
    auth_client, client, db_session, _db
):
    await _activate(auth_client)
    owner, rows = await _seed(db_session)
    key = str(uuid4())
    body = _body(rows)
    created = await auth_client.post(BASE, json=body, headers=_key(key))
    assert created.status_code == 201, created.text
    invite = await auth_client.post(
        "/api/v1/team/invites", json={"email": "ex18-checker@example.com", "role": "admin"}
    )
    accepted = await client.post(
        "/api/v1/auth/accept-invite",
        json={
            "token": invite.json()["token"],
            "name": "Synthetic checker",
            "password": "supersecret",
        },
    )
    assert accepted.status_code == 201, accepted.text
    checker_headers = {"Authorization": f"Bearer {accepted.json()['token']['access_token']}"}
    lookup = await client.get(f"{BASE}/commands/{key}", headers=checker_headers)
    assert lookup.status_code == 404, lookup.text
    unknown = await auth_client.get(f"{BASE}/commands/{uuid4()}")
    assert unknown.status_code == 404, unknown.text

    # The same existing expense-command key namespace must span operations.
    cross_key = str(uuid4())
    db_session.add(
        ExpenseCommand(
            org_id=owner.org_id,
            employee_id=owner.id,
            command_key=cross_key,
            operation="report_created",
            payload_hash="1" * 64,
            target_id=rows[0].id,
            result_json='{"id":"synthetic-prior-result"}',
        )
    )
    await db_session.commit()
    before = await _snapshot(_db, owner.org_id)
    conflict = await auth_client.post(BASE, json=body, headers=_key(cross_key))
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "expense_command_payload_conflict"
    assert await _snapshot(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_lookup_is_tenant_scoped_and_read_routes_preserve_permission_guards(
    auth_client, db_session, role_client
):
    await _activate(auth_client)
    _, rows = await _seed(db_session, 1)
    key = str(uuid4())
    created = await auth_client.post(BASE, json=_body(rows), headers=_key(key))
    assert created.status_code == 201, created.text
    other_owner = await role_client("owner")
    await _activate(other_owner)
    lookup = await other_owner.get(f"{BASE}/commands/{key}")
    assert lookup.status_code == 404, lookup.text
    for path in ("/eligible", "/page"):
        response = await other_owner.get(BASE + path)
        assert response.status_code == 200 and response.json()["total"] == 0
    employee = await role_client("user")
    for path in (f"/commands/{key}", "/eligible", "/page"):
        denied = await employee.get(BASE + path)
        assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_module_disable_and_offboarding_refuse_receipt_readback(auth_client, db_session):
    await _activate(auth_client)
    owner, rows = await _seed(db_session, 1)
    key = str(uuid4())
    assert (await auth_client.post(BASE, json=_body(rows), headers=_key(key))).status_code == 201
    disabled = await auth_client.put("/api/v1/modules/expenses", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    denied = await auth_client.get(f"{BASE}/commands/{key}")
    assert denied.status_code == 403, denied.text
    await _activate(auth_client)
    membership = await db_session.scalar(
        select(Membership).where(Membership.org_id == owner.org_id, Membership.user_id == owner.id)
    )
    membership.status = "suspended"
    await db_session.commit()
    offboarded = await auth_client.get(f"{BASE}/commands/{key}")
    assert offboarded.status_code == 401, offboarded.text


@pytest.mark.asyncio
async def test_demoted_creator_cannot_read_reimbursement_receipt_through_expense_aliases(
    auth_client, db_session, _db
):
    await _activate(auth_client)
    owner, rows = await _seed(db_session, 1)
    key = str(uuid4())
    assert (await auth_client.post(BASE, json=_body(rows), headers=_key(key))).status_code == 201
    member = await db_session.scalar(
        select(Membership).where(Membership.org_id == owner.org_id, Membership.user_id == owner.id)
    )
    member.role = UserRole.user
    member.is_expense_approver = False
    owner.role = UserRole.user
    owner.is_expense_approver = False
    await db_session.commit()
    before = await _snapshot(_db, owner.org_id)
    for path in (
        f"{BASE}/commands/{key}",
        f"/api/v1/expenses/commands/{key}",
        f"/api/v1/expenses/commands/{key}/result",
    ):
        denied = await auth_client.get(path)
        assert denied.status_code == 403, denied.text
        assert rows[0].title not in denied.text
    assert await _snapshot(_db, owner.org_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "enqueue", "receipt"])
async def test_intent_or_receipt_failure_rolls_back_batch_links_and_all_effects(
    auth_client, db_session, _db, monkeypatch, failure
):
    await _activate(auth_client)
    owner, rows = await _seed(db_session)
    before = await _snapshot(_db, owner.org_id)
    if failure == "audit":
        original = audit.record

        async def no_batch_audit(*args, **kwargs):
            if len(args) > 1 and args[1] == audit.A.REIMBURSE_BATCH:
                return None
            return await original(*args, **kwargs)

        monkeypatch.setattr(audit, "record", no_batch_audit)
    elif failure == "enqueue":

        async def no_queue(*args, **kwargs):
            raise AppError("Synthetic queue refusal", code="synthetic_queue_refusal", status=503)

        monkeypatch.setattr(jobs, "enqueue", no_queue)
    else:
        # Refuse the actual receipt INSERT, after the business object exists in
        # the transaction. This does not alter the command implementation.
        from sqlalchemy.exc import OperationalError

        engine = db_session.bind.sync_engine

        def no_receipt(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("INSERT INTO EXPENSE_COMMANDS"):
                raise OperationalError(statement, {}, RuntimeError("synthetic receipt refusal"))

        event.listen(engine, "before_cursor_execute", no_receipt)
    try:
        if failure == "receipt":
            # ASGITransport deliberately re-raises unhandled database errors.
            # The observable invariant is the rollback, not a mocked HTTP 500.
            with pytest.raises(Exception, match="synthetic receipt refusal"):
                await auth_client.post(BASE, json=_body(rows), headers=_key())
        else:
            response = await auth_client.post(BASE, json=_body(rows), headers=_key())
            assert response.status_code == 503, response.text
    finally:
        if failure == "receipt":
            event.remove(engine, "before_cursor_execute", no_receipt)
    assert await _snapshot(_db, owner.org_id) == before


@pytest.mark.asyncio
async def test_eligible_pages_include_all_payable_states_and_exclude_links(auth_client, db_session):
    await _activate(auth_client)
    owner, rows = await _seed(db_session, 107, hook=False)
    rows[0].status = "draft"
    rows[1].payout_batch_id = str(uuid4())
    await db_session.commit()
    observed = []
    for page in (1, 2):
        response = await auth_client.get(
            f"{BASE}/eligible", params={"page": page, "page_size": 100}
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data["total"], data["page"], data["page_size"]) == (105, page, 100)
        assert len(data["items"]) == (100 if page == 1 else 5)
        assert all(row["version"] >= 3 for row in data["items"])
        assert all(
            row["status"] in ("approved", "marked_for_reimbursement") for row in data["items"]
        )
        observed.extend(row["id"] for row in data["items"])
    assert len(observed) == len(set(observed)) == 105
    assert set(observed) == {row.id for row in rows[2:]}
    past_end = await auth_client.get(f"{BASE}/eligible?page=3&page_size=100")
    assert past_end.json()["items"] == [] and past_end.json()["total"] == 105
    assert (await auth_client.get(f"{BASE}/eligible?page_size=101")).status_code == 422

    # Foreign-owned rows never enter either count or items.
    foreign = Organization(name="Synthetic foreign org")
    db_session.add(foreign)
    await db_session.flush()
    db_session.add(
        ExpenseReport(
            org_id=foreign.id,
            employee_id=owner.id,
            employee_name="Foreign fixture",
            title="Foreign approved fixture",
            status="approved",
            currency="EUR",
            total=Decimal("1"),
            vat_total=Decimal("0"),
            version=1,
        )
    )
    await db_session.commit()
    own = await auth_client.get(f"{BASE}/eligible")
    assert own.json()["total"] == 105
    assert "Foreign approved fixture" not in own.text


@pytest.mark.asyncio
async def test_batch_pages_have_exact_counts_and_constant_query_growth(auth_client, db_session):
    await _activate(auth_client)
    owner, rows = await _seed(db_session, 22, hook=False)
    batches = [
        ReimbursementBatch(
            org_id=owner.org_id,
            method="bank_transfer",
            status="open",
            total_eur=Decimal("0.10"),
            created_by=owner.email,
            created_by_id=owner.id,
        )
        for _ in range(21)
    ]
    db_session.add_all(batches)
    await db_session.flush()
    for index, row in enumerate(rows):
        row.payout_batch_id = batches[min(index, 20)].id
    await db_session.commit()
    query_counts = []
    engine = db_session.bind.sync_engine
    for size in (1, 20):
        selects = []

        def track(_conn, _cursor, statement, _parameters, _context, _many, collected=selects):
            normalized = statement.lstrip().lower()
            if normalized.startswith("select") and (
                "reimbursement_batches" in normalized or "expense_reports" in normalized
            ):
                collected.append(statement)

        event.listen(engine, "before_cursor_execute", track)
        try:
            response = await auth_client.get(f"{BASE}/page?page=1&page_size={size}")
        finally:
            event.remove(engine, "before_cursor_execute", track)
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data["total"], len(data["items"])) == (21, size)
        expected = {batch.id: 1 for batch in batches}
        expected[batches[-1].id] = 2
        assert all(row["report_count"] == expected[row["id"]] for row in data["items"])
        query_counts.append(len(selects))
    assert query_counts[0] == query_counts[1]
    assert 1 <= query_counts[0] <= 3
    final = await auth_client.get(f"{BASE}/page?page=2&page_size=20")
    assert len(final.json()["items"]) == 1 and final.json()["total"] == 21
    assert (await auth_client.get(f"{BASE}/page?page_size=101")).status_code == 422


@pytest.mark.asyncio
async def test_one_thousand_unique_reports_commit_once_with_exact_count(
    auth_client, db_session, _db
):
    await _activate(auth_client)
    owner, rows = await _seed(db_session, 1000)
    response = await auth_client.post(BASE, json=_body(rows), headers=_key())
    assert response.status_code == 201, response.text
    assert response.json()["report_count"] == 1000
    assert response.json()["total_eur"] == "150.00"
    state = await _snapshot(_db, owner.org_id)
    assert len(state["reimbursement_batches"]) == len(state["expense_commands"]) == 1
    assert len(state["expense_reports"]) == 1000
    assert {row["payout_batch_id"] for row in state["expense_reports"]} == {response.json()["id"]}


@pytest.mark.asyncio
async def test_legacy_batch_list_refuses_implicit_truncation_and_explicit_pages_cover_all_rows(
    auth_client, db_session
):
    await _activate(auth_client)
    owner, _ = await _seed(db_session, 0, hook=False)
    batches = [
        ReimbursementBatch(
            org_id=owner.org_id,
            method="bank_transfer",
            status="open",
            total_eur=Decimal("0"),
            created_by=owner.email,
            created_by_id=owner.id,
        )
        for _ in range(101)
    ]
    db_session.add_all(batches)
    await db_session.commit()
    implicit = await auth_client.get(BASE)
    assert implicit.status_code == 422, implicit.text
    assert implicit.json()["code"] == "reimbursement_pagination_required"
    observed = []
    for page in (1, 2):
        response = await auth_client.get(BASE, params={"page": page, "page_size": 100})
        assert response.status_code == 200, response.text
        assert response.headers["X-Total-Count"] == "101"
        assert len(response.json()) == (100 if page == 1 else 1)
        observed.extend(row["id"] for row in response.json())
    assert len(observed) == len(set(observed)) == 101
    assert set(observed) == {batch.id for batch in batches}
