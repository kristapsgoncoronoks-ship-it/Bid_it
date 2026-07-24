"""Expense-report state machine (a single source of truth for the lifecycle).

The transition graph — no illegal jump can be made because every route asks this
module for the target state instead of writing `report.status` directly:

    draft ────submit───▶ submitted ───approve──▶ approved ──mark_for_reimbursement─▶ marked_for_reimbursement
      ▲   ◀──withdraw──────┘   │  ├──reject───▶ rejected            │                          │
      │                        │  └──return──▶ returned             └────────mark_reimbursed──▶ reimbursed
      └──────(edit)────────────┴──── returned ──submit──▶ submitted        (approved ─mark_reimbursed─▶ reimbursed too)

`returned` is editable by the owner (like `draft`) so a sent-back report can be
fixed and resubmitted. `rejected` and `reimbursed` are terminal.

This mirrors the invoice workflow's `assert_transition` pattern but is a distinct,
simpler graph — expenses do not reuse the 14-state invoice machine.
"""

from __future__ import annotations

from fastapi import HTTPException, status

STATES = (
    "draft",
    "submitted",
    "partially_approved",
    "approved",
    "rejected",
    "returned",
    "marked_for_reimbursement",
    "reimbursed",
)

# States in which the OWNER may still edit / delete the report and its items.
EDITABLE = frozenset({"draft", "returned"})
# States from which no further transition is possible.
TERMINAL = frozenset({"rejected", "reimbursed"})
# States in which an approval-chain decision (approve/reject/return) may be made.
IN_APPROVAL = frozenset({"submitted", "partially_approved"})

# action → (set of legal source states, target state). Actor authorisation
# (owner vs approver, segregation of duties) is enforced at the route layer; this
# table encodes only which status→status moves are legal.
_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "submit": (frozenset({"draft", "returned"}), "submitted"),
    "withdraw": (frozenset({"submitted"}), "draft"),
    "approve": (frozenset({"submitted"}), "approved"),
    "reject": (frozenset({"submitted"}), "rejected"),
    "return_for_correction": (frozenset({"submitted"}), "returned"),
    "mark_for_reimbursement": (frozenset({"approved"}), "marked_for_reimbursement"),
    "mark_reimbursed": (frozenset({"approved", "marked_for_reimbursement"}), "reimbursed"),
}

# Actions an approver/finance actor performs (vs owner-driven submit/withdraw).
APPROVER_ACTIONS = frozenset(
    {"approve", "reject", "return_for_correction", "mark_for_reimbursement", "mark_reimbursed"}
)
OWNER_ACTIONS = frozenset({"submit", "withdraw"})

# Legacy alias kept so existing callers/tests using "reimburse" keep working.
_ALIASES = {"reimburse": "mark_reimbursed"}


def canonical(action: str) -> str:
    return _ALIASES.get(action, action)


def target_for(action: str, current_status: str) -> str:
    """The status a legal `action` moves `current_status` to, or raise 409/422.

    409 when the action is known but illegal from the current state; 422 when the
    action name itself is unknown.
    """
    act = canonical(action)
    spec = _TRANSITIONS.get(act)
    if spec is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown action '{action}'")
    sources, target = spec
    if current_status not in sources:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot {act.replace('_', ' ')} a {current_status} report "
            f"(allowed from: {', '.join(sorted(sources))}).",
        )
    return target


def can(action: str, current_status: str) -> bool:
    """True if `action` is legal from `current_status` (no exception)."""
    spec = _TRANSITIONS.get(canonical(action))
    return spec is not None and current_status in spec[0]


def is_editable(current_status: str) -> bool:
    return current_status in EDITABLE
