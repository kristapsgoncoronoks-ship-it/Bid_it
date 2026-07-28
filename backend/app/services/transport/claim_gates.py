"""The shared claim-gate predicates (R3) — load-bearing, deliberately centralized.

`is_synthetic()` is THE single predicate for "this claim line is not tied to
one real, registered invoice". Fleet Fuel's own BA is explicit about why it
must be exactly one function (`docs/plan/shared/specs/BA_fleet_fuel.md` C2):

    "A pack containing ANY synthetic line CANNOT be filed. The same predicate
    is used by: the lock gate ... the checklist gate ... the readiness check
    ... the workbook builder. This centralization is deliberate: so they all
    block the same set of synthetic refs."

None of those four consumers exist yet (they are M3 follow-on work — the lock
gate, checklist gate, readiness check and workbook builder are explicitly OUT
OF SCOPE for WO-49, the milestone opener). This module exists now, alone,
because the INTERFACE is the load-bearing artifact: every future gate must
import THIS function rather than re-deriving the pattern, or the drift R3
exists to prevent creeps back in on day one. Get the interface right before
there is a second implementation to reconcile.

WHY THIS FAILS TOWARD BLOCKING (fail-closed), not open
---------------------------------------------------------
A synthetic ref means "we cannot prove which real invoice this euro amount
belongs to". Filing it anyway risks the WHOLE claim on a probabilistic guess;
CJEU case law and the Fleet Fuel BA agree the correct failure mode is to
refuse the line (and by extension the pack) rather than invent a match. Every
future consumer of `is_synthetic()` must preserve this: a bug that makes the
predicate return `False` too often is a silent forfeiture-of-money bug, not a
convenience.
"""

from __future__ import annotations


def is_synthetic(ref: str, vat_id: str | None = None) -> bool:
    """R3 — Art. 9/15 Dir. 2008/9/EC (a claim line must be tied to a real,
    registered invoice); harvested verbatim from `BA_fleet_fuel.md` C2 /
    the R3 row of its requirements table:

        is_synthetic(ref, vat_id) ==
            "INPUT" in ref or ref.startswith("ALL:") or ref == "UNMATCHED"
            or "INPUT" in str(vat_id)

    `ref` is the claim line's `invoice_ref` (never the buyer-supplied
    aggregate, never the resolved invoice number placeholder). `INPUT` in
    either the ref or the vat_id marks a hand-entered placeholder value (the
    Fleet Fuel capture UI's own convention for "we don't have this yet" —
    R45 reuses the identical substring check for `_field_ok`). `ALL:` marks a
    country-level aggregate a period once collapsed unresolved transactions
    into (never a real invoice). `UNMATCHED` is the terminal state of
    `_resolve_inv`'s note-matching chain when nothing else resolved it.

    A pack containing ANY line for which this returns `True` cannot be filed
    — see the module docstring for which future gates enforce that.
    """
    return (
        ("INPUT" in ref)
        or ref.startswith("ALL:")
        or (ref == "UNMATCHED")
        or ("INPUT" in str(vat_id))
    )
