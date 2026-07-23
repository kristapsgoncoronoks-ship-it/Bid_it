"""Explicit lifecycle state machine for issued invoices (INV-3).

The STORED `lifecycle` column holds the workflow state; the AR/payment status
(open/partial/paid/overdue/credited) and delivery (sent/viewed) are derived on top
of the `issued` state by `issued_status.status_of`. Default `issued` reproduces
the historical born-final behaviour.
"""

from __future__ import annotations

from fastapi import HTTPException, status

DRAFT = "draft"
APPROVED = "approved"
ISSUED = "issued"
DISPUTED = "disputed"
WRITTEN_OFF = "written_off"
CANCELLED = "cancelled"

STORED_STATES = (DRAFT, APPROVED, ISSUED, DISPUTED, WRITTEN_OFF, CANCELLED)
# Only a draft may be edited or deleted; everything else is immutable.
EDITABLE = frozenset({DRAFT})

# action → (legal source states, target state). Actor authz is enforced at the
# route layer; this table encodes only which lifecycle moves are legal.
_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "approve": (frozenset({DRAFT}), APPROVED),
    "issue": (frozenset({DRAFT, APPROVED}), ISSUED),
    "dispute": (frozenset({ISSUED}), DISPUTED),
    "undispute": (frozenset({DISPUTED}), ISSUED),
    "write_off": (frozenset({ISSUED, DISPUTED}), WRITTEN_OFF),
    "cancel_draft": (frozenset({DRAFT, APPROVED}), CANCELLED),
}


def target_for(action: str, current: str) -> str:
    spec = _TRANSITIONS.get(action)
    if spec is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown action '{action}'")
    sources, target = spec
    if current not in sources:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot {action.replace('_', ' ')} a {current} invoice "
            f"(allowed from: {', '.join(sorted(sources))}).",
        )
    return target


def is_editable(lifecycle: str) -> bool:
    return lifecycle in EDITABLE
