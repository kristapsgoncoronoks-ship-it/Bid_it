"""Guards for operating on many records at once (L-4).

WHY THIS MODULE EXISTS SEPARATELY FROM ANY OPERATION
----------------------------------------------------
Bulk actions collide with four things this codebase already guarantees per
record: audit old→new, separation of duties, the opaque 404, and quota metering.
The collision is not with any single operation — it is with the SHAPE of "do this
to N things". So the guards live here, once, and each operation supplies only its
per-record work.

THE FOUR GUARDS
---------------

1. **The agreed count.** The client sends the number of records it DISPLAYED when
   the human clicked. If the server counts a different number, the list moved
   under them — someone else acted, a filter re-evaluated, a job finished — and
   their selection is no longer what they were looking at. The operation aborts
   rather than applying to a set the human never saw. This is the guard that
   turns "select all → delete" from a hazard into a transaction.

2. **Structured outcomes, with domain skips first-class.** A bulk result is not a
   boolean and not an exception count. Every record comes back with what happened
   to it and WHY. A skip is an ordinary outcome, not a failure: "already
   acknowledged" is the system working, and burying it in an error count teaches
   the operator to ignore error counts.

3. **A reversal record derived MECHANICALLY from the write.** Not hand-authored.
   Whatever the operation actually did is what the undo describes — a
   hand-written inverse drifts from the forward path the first time either
   changes, and a wrong undo is worse than none.

4. **Filter-selection is refused outright for irreversible actions.** "Everything
   matching this filter" is a set the human never enumerated and cannot verify.
   For anything that cannot be undone, only an EXPLICIT list of ids is accepted.
   A filter may still drive a reversible action, where being wrong is recoverable.

WHAT THIS MODULE DOES NOT DO
----------------------------
It performs no work of its own and knows nothing about any domain. It never
loosens a per-record rule: each record still goes through the same service call,
the same permission, the same audit write it would have gone through alone. Bulk
is a way to ISSUE many operations, never a way to skip what one of them requires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.errors import ConflictError, ValidationError

# A bounded batch. Not a performance limit — a blast-radius limit: past a few
# hundred records nobody has meaningfully reviewed what they selected, and the
# agreed-count guard stops protecting anything real.
MAX_BATCH = 200


class Selection(str, Enum):
    """How the caller chose the records."""

    # A list of ids the human can see and count. The only mode allowed to drive
    # something irreversible.
    EXPLICIT = "explicit"
    # "Everything matching what I am looking at." Convenient, unverifiable.
    FILTER = "filter"


# Outcome kinds. `skipped` is deliberately NOT a failure — see guard 2.
APPLIED = "applied"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass
class Outcome:
    """What happened to ONE record, and why. `reason` is written for the operator
    on a skip or a failure, and is None when the record was simply applied."""

    ref_id: str
    result: str
    reason: str | None = None


@dataclass
class BulkResult:
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return sum(1 for o in self.outcomes if o.result == APPLIED)

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.result == SKIPPED)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.result == FAILED)

    @property
    def applied_ids(self) -> list[str]:
        """The ids that actually changed — the mechanical basis for a reversal
        (guard 3). Derived from the outcomes the write produced, never from the
        ids that were requested."""
        return [o.ref_id for o in self.outcomes if o.result == APPLIED]


def normalise_ids(ids: list[str]) -> list[str]:
    """De-duplicate while preserving order, and reject an empty selection.

    Duplicates are removed rather than rejected: a client that sends the same id
    twice meant it once, and applying twice would double-count the audit trail.
    Order is preserved so the outcome list reads in the order the operator saw.
    """
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    if not out:
        raise ValidationError("Select at least one record.", code="bulk_empty_selection")
    if len(out) > MAX_BATCH:
        raise ValidationError(
            f"Select at most {MAX_BATCH} records at a time. "
            "Past that nobody has really reviewed what they picked.",
            code="bulk_too_many",
        )
    return out


def require_explicit_selection(selection: Selection, *, action: str) -> None:
    """Guard 4. Refuse a filter-driven selection for something that cannot be
    undone.

    Raised as a ValidationError rather than silently narrowing the scope: a
    caller that asked to destroy "everything matching this filter" must be told
    no, not quietly given something else.
    """
    if selection is not Selection.EXPLICIT:
        raise ValidationError(
            f"{action} cannot be applied to a filter-based selection. "
            "Select the specific records so you can see exactly what you are changing.",
            code="bulk_filter_not_allowed",
        )


def check_agreed_count(ids: list[str], agreed_count: int | None) -> None:
    """Guard 1. The client states how many records it DISPLAYED; refuse if the
    server's set is a different size.

    `None` is accepted and means the caller did not claim a count — an API script
    rather than a human clicking a list. The guard protects a person against a
    list that moved under them; a script has no list and nothing to protect.
    Making it mandatory would only teach callers to echo `len(ids)` back, which
    guards nothing at all.
    """
    if agreed_count is None:
        return
    if agreed_count != len(ids):
        raise ConflictError(
            f"You selected {agreed_count} record(s) but {len(ids)} were received. "
            "The list changed since you looked at it — reload and select again.",
            code="bulk_count_mismatch",
        )
