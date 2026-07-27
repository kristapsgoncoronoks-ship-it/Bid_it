"""Supplier-invoice lifecycle state machine (AP review & approval).

The 11-state lifecycle an invoice moves through from draft to archive. This
module is the SINGLE SOURCE OF TRUTH for *which* state changes are legal — every
route computes a target state and calls `assert_transition(...)`, which raises on
an illegal move. It is pure domain logic: no DB, no I/O, so it is trivially unit
-testable and reused by the route layer and the tests alike.

Two orthogonal failure modes the routes distinguish:
  - an INVALID TRANSITION for the current state  → 422 (the action makes no sense
    here, e.g. "approve" a paid invoice);
  - a STALE VERSION (optimistic concurrency)     → 409 (someone else changed the
    row since you read it) — handled by `assert_version` below.

The lifecycle runs ALONGSIDE the legacy aging `Invoice.status`
(draft/pending/paid/overdue), which analytics and payments still read; the routes
sync the aging status at the paid/scheduled milestones. `workflow_state` is the
canonical AP-processing state.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.models.invoice import WorkflowState

_S = WorkflowState

# Human-facing labels (the UI reads these; keep in sync with the enum).
LABELS: dict[WorkflowState, str] = {
    _S.draft: "Draft",
    _S.submitted: "Submitted for Approval",
    _S.partially_approved: "Partially Approved",
    _S.approved: "Approved",
    _S.rejected: "Rejected",
    _S.scheduled_for_payment: "Scheduled for Payment",
    _S.partially_paid: "Partially Paid",
    _S.paid: "Paid",
    _S.disputed: "Disputed",
    _S.cancelled: "Cancelled",
    _S.archived: "Archived",
}

# The legal transition graph. A key's value is the set of states it may move to.
# `cancelled` is reachable from any non-terminal state (added below). Every state
# except `archived` can also be cancelled; `archived` is fully terminal.
TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    # submit → submitted; the policy engine then advances it.
    _S.draft: frozenset({_S.submitted}),
    # one approval step done with more remaining → partially_approved; last step →
    # approved; a rejection → rejected; return-for-correction → draft.
    _S.submitted: frozenset({_S.partially_approved, _S.approved, _S.rejected, _S.draft}),
    _S.partially_approved: frozenset({_S.approved, _S.rejected, _S.draft}),
    # approved → schedule payment, dispute, or a CONTROLLED correction back to
    # draft (which invalidates the approval chain — see the route).
    _S.approved: frozenset({_S.scheduled_for_payment, _S.disputed, _S.draft}),
    _S.rejected: frozenset({_S.draft, _S.archived}),
    _S.scheduled_for_payment: frozenset({_S.partially_paid, _S.paid, _S.disputed}),
    _S.partially_paid: frozenset({_S.paid, _S.disputed}),
    _S.paid: frozenset({_S.disputed, _S.archived}),
    # dispute resolves either back to rework (draft), forward (re-approved /
    # re-scheduled), or is written off (cancelled).
    _S.disputed: frozenset({_S.draft, _S.approved, _S.scheduled_for_payment}),
    _S.cancelled: frozenset({_S.archived}),
    _S.archived: frozenset(),
}

# `cancelled` is a valid exit from every state that is not already terminal.
_CANCELLABLE = frozenset(
    s for s in WorkflowState if s not in (_S.cancelled, _S.archived, _S.paid, _S.rejected)
)

# States in which the invoice's header/lines may be freely edited. After
# submission the record is effectively frozen; editing an approved+ invoice needs
# the explicit controlled-correction path (reopen → draft) — see the route.
EDITABLE: frozenset[WorkflowState] = frozenset({_S.draft})

# States where the record is LOCKED (approved and everything downstream). A locked
# invoice cannot be edited or re-submitted without a controlled correction.
LOCKED: frozenset[WorkflowState] = frozenset(
    {
        _S.approved,
        _S.scheduled_for_payment,
        _S.partially_paid,
        _S.paid,
        _S.archived,
    }
)

# Terminal states — no further transitions (archived) / only archival (cancelled).
TERMINAL: frozenset[WorkflowState] = frozenset({_S.archived})


def allowed_targets(frm: WorkflowState) -> frozenset[WorkflowState]:
    """Every state `frm` may legally move to (including cancel where allowed)."""
    targets = set(TRANSITIONS.get(frm, frozenset()))
    if frm in _CANCELLABLE:
        targets.add(_S.cancelled)
    return frozenset(targets)


def can_transition(frm: WorkflowState, to: WorkflowState) -> bool:
    return to in allowed_targets(frm)


def assert_transition(frm: WorkflowState, to: WorkflowState) -> None:
    """Reject an illegal state change (422). This is the choke point every
    transition route calls before mutating `workflow_state`."""
    if not can_transition(frm, to):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Illegal transition: {frm.value} → {to.value}. "
            f"Allowed from {frm.value}: {sorted(t.value for t in allowed_targets(frm))}.",
        )


def assert_version(current: int, expected: int | None) -> None:
    """Optimistic-concurrency gate. The client sends the `version` it read; if the
    row has moved on since, raise 409 so two reviewers can't silently overwrite
    each other. `expected is None` means the caller opted out (server-only path)."""
    if expected is not None and expected != current:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This invoice was changed by someone else (your version {expected}, "
            f"current {current}). Reload and re-apply your changes.",
        )


def is_editable(state: WorkflowState) -> bool:
    return state in EDITABLE


def is_locked(state: WorkflowState) -> bool:
    return state in LOCKED
