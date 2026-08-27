"""Every state-changing payout route loads its row FOR UPDATE (WO-Y).

WHY A STRUCTURAL TEST AND NOT ANOTHER RACE
------------------------------------------
The concurrency proofs — `test_payment_run_pay_concurrency.py` and
`test_reimbursement_pay_concurrency.py` — do something subtly weaker than they
appear to: each one REPLICATES the route's `with_for_update()` in the test body
and races the SERVICE. They prove the pattern is sound. They do not notice if a
route stops using it. Deleting `lock=True` from a route leaves both of them
green, which was verified rather than assumed while writing this file.

That is not a flaw worth removing — racing the real route through the ASGI
client would need a Postgres-backed app fixture and would still be timing
dependent. The gap is better closed from the other side: the races prove the
lock works, and this proves the routes take it.

WHAT IT CHECKS
--------------
For each payout router, the set of routes that call a MUTATING service function
is recomputed from the module's own AST, and every one of them must pass
`lock=True` to its loader. Nothing here is a hand-kept list of route names: a
new route that settles, cancels or exports a payout is covered the day it is
written, and a route that stops mutating stops being required to lock.

This is the same failure shape WO-Y was written to close, and the reason the
whole file exists is that it found one. `_load`'s own comment in
`routes/reimbursements.py` has always said the lock serialises "pay/cancel" —
and `cancel_batch` did not take it. The docstring described an intention; the
code implemented half of it; nothing compared the two.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[1] / "app" / "api" / "routes"

#: Service calls that CHANGE a payout's state. A route that makes one of these
#: is deciding on money and must have serialized on the row first. Recorded as
#: bare attribute names so `reimbursement.mark_paid` and `pr.mark_paid` both
#: match regardless of how the module was imported.
MUTATORS = frozenset({"mark_paid", "cancel_batch", "record_export", "cancel_run"})

#: The per-module loader whose `lock=True` takes the row lock.
LOADERS = {
    "reimbursements.py": "_load",
    "payment_runs.py": "_load",
}


def _is_route(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            if target.value.id == "router":
                return True
    return False


def _calls(fn: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)]


def _called_names(fn: ast.AST) -> set[str]:
    names = set()
    for call in _calls(fn):
        target = call.func
        if isinstance(target, ast.Attribute):
            names.add(target.attr)
        elif isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _loads_with_lock(fn: ast.AST, loader: str) -> bool | None:
    """True/False if the function calls `loader`, None if it never does."""
    seen = False
    for call in _calls(fn):
        target = call.func
        name = (
            target.attr
            if isinstance(target, ast.Attribute)
            else target.id
            if isinstance(target, ast.Name)
            else None
        )
        if name != loader:
            continue
        seen = True
        for kw in call.keywords:
            if kw.arg == "lock" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                return True
    return False if seen else None


def test_every_mutating_payout_route_takes_the_row_lock():
    checked: list[str] = []
    unlocked: list[str] = []
    for filename, loader in LOADERS.items():
        path = ROUTES / filename
        assert path.exists(), f"payout router moved: {path}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if not _is_route(node):
                continue
            if not (_called_names(node) & MUTATORS):
                continue  # a read-only route has nothing to serialize on
            locked = _loads_with_lock(node, loader)
            if locked is None:
                continue  # loads some other way; not this gate's business
            checked.append(f"{filename}::{node.name}")
            if not locked:
                unlocked.append(f"{filename}::{node.name}")

    # Positive anchor: if the AST walk silently matched nothing, an empty
    # `unlocked` would look exactly like success.
    assert len(checked) >= 3, f"expected to find the payout mutators, found {checked}"
    assert not unlocked, (
        "these payout routes change money state without loading the row FOR UPDATE: "
        f"{unlocked}. A plain read does not block, so the decision is made on a "
        "value another transaction may already have replaced."
    )


def test_the_loader_still_documents_what_it_covers():
    """The docstring naming pay/cancel is what this file compared the code
    against. If someone narrows the claim, the comparison it invites should
    narrow with it — deliberately, not by the sentence quietly going stale."""
    text = (ROUTES / "reimbursements.py").read_text(encoding="utf-8")
    assert "Serialize state-changing operations (pay/cancel)" in text
