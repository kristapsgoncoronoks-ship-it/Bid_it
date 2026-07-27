"""Unit tests for the invoice review/approval domain logic (Phase 08):
the state machine (transitions + concurrency guards) and the approval-policy
evaluation + chain lifecycle. Pure logic + a few DB-backed evaluation cases."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.approval import (
    STEP_APPROVED,
    STEP_PENDING,
    STEP_REJECTED,
    ApprovalPolicy,
    ApprovalStep,
)
from app.models.invoice import Invoice, WorkflowState
from app.models.organization import Organization
from app.models.vendor import Vendor
from app.services import approval_policy as ap
from app.services import invoice_workflow as wf

S = WorkflowState


# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #


def test_happy_path_transitions_are_legal():
    chain = [
        (S.draft, S.submitted),
        (S.submitted, S.partially_approved),
        (S.partially_approved, S.approved),
        (S.approved, S.scheduled_for_payment),
        (S.scheduled_for_payment, S.partially_paid),
        (S.partially_paid, S.paid),
        (S.paid, S.archived),
    ]
    for frm, to in chain:
        assert wf.can_transition(frm, to), f"{frm}→{to} should be legal"


@pytest.mark.parametrize(
    "frm,to",
    [
        (S.paid, S.approved),  # cannot un-pay
        (S.archived, S.draft),  # terminal
        (S.draft, S.approved),  # must go through submission
        (S.approved, S.submitted),  # cannot re-submit an approved (use correction→draft)
        (S.rejected, S.approved),  # a rejection is final until reworked
        (S.paid, S.cancelled),  # paid is not freely cancellable
    ],
)
def test_illegal_transitions_are_rejected(frm, to):
    assert not wf.can_transition(frm, to)
    with pytest.raises(HTTPException) as ei:
        wf.assert_transition(frm, to)
    assert ei.value.status_code == 422


def test_return_and_controlled_correction_paths():
    assert wf.can_transition(S.submitted, S.draft)  # return for correction
    assert wf.can_transition(S.partially_approved, S.draft)
    assert wf.can_transition(S.approved, S.draft)  # controlled correction (reopen)


def test_cancellable_from_active_states_only():
    assert wf.can_transition(S.draft, S.cancelled)
    assert wf.can_transition(S.submitted, S.cancelled)
    assert not wf.can_transition(S.paid, S.cancelled)
    assert not wf.can_transition(S.archived, S.cancelled)


def test_editable_and_locked_sets():
    assert wf.is_editable(S.draft)
    assert not wf.is_editable(S.submitted)  # frozen once submitted
    assert wf.is_locked(S.approved) and wf.is_locked(S.paid)
    assert not wf.is_locked(S.draft)


def test_optimistic_concurrency_guard():
    wf.assert_version(3, 3)  # match → ok
    wf.assert_version(3, None)  # opt-out → ok
    with pytest.raises(HTTPException) as ei:
        wf.assert_version(4, 3)  # stale
    assert ei.value.status_code == 409


# --------------------------------------------------------------------------- #
# Policy matching (pure)
# --------------------------------------------------------------------------- #


def _inv(**kw) -> Invoice:
    base = dict(total=Decimal("100"), total_eur=Decimal("100"))
    base.update(kw)
    return Invoice(**base)


def test_amount_threshold_matching():
    p = ApprovalPolicy(name="big", min_amount=Decimal("500"))
    assert not ap.matches(p, _inv(total_eur=Decimal("499.99")))
    assert ap.matches(p, _inv(total_eur=Decimal("500")))
    assert ap.matches(p, _inv(total_eur=Decimal("5000")))


def test_dimension_and_supplier_matching():
    p = ApprovalPolicy(name="dept", department_id="dept-1", vendor_id="v-9")
    assert ap.matches(p, _inv(department_id="dept-1", vendor_id="v-9"))
    assert not ap.matches(p, _inv(department_id="dept-2", vendor_id="v-9"))
    assert not ap.matches(p, _inv(department_id="dept-1", vendor_id="v-1"))


def test_unset_criteria_are_wildcards():
    p = ApprovalPolicy(name="catch-all")  # no criteria
    assert ap.matches(p, _inv(department_id="anything", total_eur=Decimal("1")))


def test_legal_entity_matching():
    p = ApprovalPolicy(name="entity", legal_entity_id="ent-1")
    assert ap.matches(p, _inv(legal_entity_id="ent-1"))
    assert not ap.matches(p, _inv(legal_entity_id=None))


# --------------------------------------------------------------------------- #
# Chain state derivation (pure)
# --------------------------------------------------------------------------- #


def _steps(*statuses) -> list[ApprovalStep]:
    return [ApprovalStep(seq=i, status=s) for i, s in enumerate(statuses)]


def test_chain_state_derivation():
    assert ap.chain_state(_steps(STEP_PENDING, STEP_PENDING)) == S.submitted
    assert ap.chain_state(_steps(STEP_APPROVED, STEP_PENDING)) == S.partially_approved
    assert ap.chain_state(_steps(STEP_APPROVED, STEP_APPROVED)) == S.approved
    assert ap.chain_state(_steps(STEP_APPROVED, STEP_REJECTED)) == S.rejected


def test_pending_step_is_lowest_seq_pending():
    steps = _steps(STEP_APPROVED, STEP_PENDING, STEP_PENDING)
    nxt = ap.pending_step(steps)
    assert nxt is not None and nxt.seq == 1


def test_is_assigned_approver():
    user = type("U", (), {"id": "u1", "is_platform_admin": False})()
    admin = type("U", (), {"id": "u2", "is_platform_admin": True})()
    open_step = ApprovalStep(seq=0, approver_id=None)
    named = ApprovalStep(seq=0, approver_id="u1")
    assert ap.is_assigned_approver(open_step, user)  # unassigned → anyone
    assert ap.is_assigned_approver(named, user)  # the named approver
    assert not ap.is_assigned_approver(
        named, type("U", (), {"id": "u9", "is_platform_admin": False})()
    )
    assert ap.is_assigned_approver(named, admin)  # platform admin override


def test_skip_pending_marks_only_pending():
    steps = _steps(STEP_APPROVED, STEP_PENDING, STEP_PENDING)
    ap.skip_pending(steps)
    assert [s.status for s in steps] == [STEP_APPROVED, "skipped", "skipped"]


# --------------------------------------------------------------------------- #
# Policy evaluation + chain build (DB-backed)
# --------------------------------------------------------------------------- #


async def _org_and_invoice(db, *, amount="1000", dept=None, vendor_id=None):
    org = Organization(name="WF Co")
    db.add(org)
    await db.flush()
    v = Vendor(org_id=org.id, name="Acme")
    db.add(v)
    await db.flush()
    inv = Invoice(
        org_id=org.id,
        vendor_id=vendor_id or v.id,
        invoice_number="INV-1",
        issue_date=date(2026, 5, 1),
        currency="EUR",
        total=Decimal(amount),
        total_eur=Decimal(amount),
        department_id=dept,
    )
    db.add(inv)
    await db.flush()
    return org, inv


@pytest.mark.asyncio
async def test_evaluate_picks_first_matching_by_priority(db_session):
    org, inv = await _org_and_invoice(db_session, amount="1000")
    # Two matching policies; lower priority number wins.
    db_session.add(
        ApprovalPolicy(org_id=org.id, name="low", priority=200, min_amount=Decimal("100"))
    )
    db_session.add(
        ApprovalPolicy(org_id=org.id, name="high", priority=10, min_amount=Decimal("500"))
    )
    # A non-matching high-amount policy is skipped.
    db_session.add(
        ApprovalPolicy(org_id=org.id, name="huge", priority=1, min_amount=Decimal("9999"))
    )
    await db_session.flush()
    chosen = await ap.evaluate(db_session, org.id, inv)
    assert chosen is not None and chosen.name == "high"


@pytest.mark.asyncio
async def test_evaluate_returns_none_when_no_policy_matches(db_session):
    org, inv = await _org_and_invoice(db_session, amount="50")
    db_session.add(ApprovalPolicy(org_id=org.id, name="big", min_amount=Decimal("500")))
    await db_session.flush()
    assert await ap.evaluate(db_session, org.id, inv) is None


@pytest.mark.asyncio
async def test_build_chain_sequential_with_finance_final(db_session):
    import json

    org, inv = await _org_and_invoice(db_session)
    pol = ApprovalPolicy(
        org_id=org.id,
        name="two-step+finance",
        approver_ids=json.dumps(["appr-1", "appr-2"]),
        finance_final=True,
        finance_approver_id="fin-1",
    )
    db_session.add(pol)
    await db_session.flush()
    steps = await ap.build_chain(db_session, org.id, inv, pol)
    assert [s.seq for s in steps] == [0, 1, 2]
    assert [s.approver_id for s in steps] == ["appr-1", "appr-2", "fin-1"]
    assert steps[-1].kind == "finance_final"
    assert ap.chain_state(steps) == S.submitted


@pytest.mark.asyncio
async def test_build_chain_no_policy_makes_one_open_step(db_session):
    org, inv = await _org_and_invoice(db_session)
    steps = await ap.build_chain(db_session, org.id, inv, None)
    assert len(steps) == 1 and steps[0].approver_id is None and steps[0].seq == 0
