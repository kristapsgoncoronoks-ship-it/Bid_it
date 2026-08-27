"""The claim lifecycle's edge set, pinned (WO-T).

WO-T's work order asked for "the WO-82 edge-set pin extended to the new
sanctioned edge". That pin could not be extended, because it is about a
different table: `test_wo82_overcharge_lifecycle.py` pins the OVERCHARGE
claim-back chain (`vat_overcharge_claims`), and the refund-claim lifecycle
(`vat_refund_claims`) had no equivalent at all. So this module is the missing
one, built to the same principle rather than bolted onto its neighbour.

WHAT IT PINS, AND WHY THAT SHAPE
----------------------------------
Not "these transitions are legal" — the behavioural suites already prove each
edge and each refusal, one test apiece. What has never been checked is
**completeness**: that the set of places in this codebase which can move a
claim's status is exactly the set anyone believes it to be. That is a property
no behavioural test can hold, because a test can only assert about a writer it
already knows exists.

So this scans the transport service package for every `<x>.status = "<literal>"`
assignment and asserts the (module, destination) pairs equal a table declared
here. A fifth writer, or a sixth status, or the same edge quietly duplicated
into a second module, fails by name — which is the drift WO-82's own pin was
written to prevent, and the reason WO-T put its new transition INTO
`decision.py` rather than into a module of its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
TRANSPORT_SERVICES = BACKEND / "app" / "services" / "transport"

#: (module, destination status) -> the transition it performs, and its gate.
#: Every entry is owner-decided or harvested; none arrived by convenience.
SANCTIONED_EDGES: dict[tuple[str, str], str] = {
    ("lock.py", "submitted"): "draft -> submitted, the D5 gate chain (WO-51/WO-59)",
    ("lock.py", "withdrawn"): "submitted -> withdrawn, R5's lock release (WO-51/WO-94)",
    ("decision.py", "approved"): "submitted -> approved, in full or after a partial (WO-L §13)",
    ("decision.py", "rejected"): "submitted -> rejected, the whole claim refused (WO-L §13)",
    ("decision.py", "paid"): "approved -> paid, the refund landing (WO-T)",
}


def _status_writers() -> set[tuple[str, str]]:
    """Every `<anything>.status = "<literal>"` in the transport service package.

    Deliberately matches on the ATTRIBUTE name rather than on the variable, so a
    writer that renamed its local from `claim` to something else is still
    caught — the point is completeness, and a scan that can be dodged by
    renaming a variable proves nothing.
    """
    found: set[tuple[str, str]] = set()
    for path in sorted(TRANSPORT_SERVICES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "status"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    found.add((path.name, node.value.value))
    return found


def test_the_claim_status_writers_are_exactly_the_sanctioned_set():
    """A new writer, a new destination, or the same edge duplicated into a
    second module fails here by name."""
    found = _status_writers()
    expected = set(SANCTIONED_EDGES)

    unsanctioned = found - expected
    assert not unsanctioned, "a claim-status transition exists that nothing decided: " + ", ".join(
        f"{m} -> '{s}'" for m, s in sorted(unsanctioned)
    )
    missing = expected - found
    assert not missing, "a sanctioned transition lost its writer: " + ", ".join(
        f"{m} -> '{s}' ({SANCTIONED_EDGES[(m, s)]})" for m, s in sorted(missing)
    )


def test_every_sanctioned_destination_is_a_real_claim_status():
    """The pin is only as good as its vocabulary: an edge to a status the model
    does not define would be a typo this table happily enshrined."""
    from app.models.transport.vat_claim import CLAIM_STATUSES

    for _module, status in SANCTIONED_EDGES:
        assert status in CLAIM_STATUSES, f"'{status}' is not a claim status"


def test_the_scan_can_actually_fail():
    """A scan that cannot fail proves nothing. This walks the same AST over a
    seeded source string carrying an unsanctioned transition, so the matcher
    itself is exercised rather than trusted."""
    seeded = 'def sneak(claim):\n    claim.status = "settled"\n'
    tree = ast.parse(seeded)
    hits = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Attribute) and target.attr == "status"
    }
    assert hits == {"settled"}
    assert "settled" not in {s for _m, s in SANCTIONED_EDGES}
